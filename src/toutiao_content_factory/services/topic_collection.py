from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from toutiao_content_factory.config import AppSettings, TopicCollectionSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService


@dataclass
class TopicSourceResult:
    source_name: str
    items: list[TopicCandidate]
    errors: list[str]


class TopicCollectionService:
    URL_DOMAIN_BLACKLIST: frozenset[str] = frozenset({
        "google.com",
        "googleapis.com",
        "newsrss.bbc.co.uk",
        "bbc.co.uk",
    })

    # 实时热榜源类型（头条热榜 / 百度热搜）：标题即热点，天然具备高讨论度
    HOT_SOURCE_TYPES = frozenset({"hot_board", "baidu_hot"})
    # 热度值达到该阈值视为"真热点"，可放宽标题型话题的质量门禁
    HOT_HEAT_THRESHOLD = 5_000_000
    # 敏感/高危话题拦截词：避免热点二创踩平台红线（自杀、恶性案件、医疗争议等）
    HOT_TOPIC_BLOCKLIST = (
        "自缢",
        "自杀",
        "跳楼",
        "轻生",
        "割腕",
        "凶杀",
        "弑",
        "奸杀",
        "分尸",
        "碎尸",
        "命案",
        "恶性案件",
        "枪击",
        "爆炸事故",
        "矿难",
        "坍塌",
        "坠机",
        "车祸",
        "中毒身亡",
        "猝死",
        "虐童",
        "性侵",
        "猥亵",
        "嫖娼",
        "毒品",
        "贩毒",
        "涉黑",
        "恐怖袭击",
        "暴恐",
        "传销",
        "缅北",
        "器官",
        "遗体",
        "尸检",
        "死刑",
        "冤案",
        "医疗事故",
        "医闹",
        "疫苗事故",
    )

    NUMBER_PATTERN = re.compile(
        r"\d+(?:\.\d+)?(?:%|倍|年|季度|个月|周|天|小时|分钟|亿元|亿美元|万元|万台|万辆|万套|万吨|万千瓦|万千瓦时|"
        r"GWh|MWh|kWh|Wh|Ah|GW|MW|kW|W|V|A)"
    )
    TITLE_NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    DISCUSSION_TOKENS = ("融资", "订单", "量产", "发布", "争议", "突破", "价格战", "出海", "裁员", "收购")
    SAVE_TOKENS = ("一图看懂", "产业链", "数据", "测算", "对比", "拆解", "复盘", "指标", "路线图")
    READ_TOKENS = ("为什么", "背后", "首次", "最新", "量产", "融资", "爆发", "拐点", "机会")
    GENERIC_MACRO_TOKENS = (
        "国民经济",
        "经济运行",
        "宏观数据",
        "社会消费品零售总额",
        "固定资产投资",
        "规上工业增加值",
        "统计局",
        "发布会",
        "中国经济数据",
        "宏观",
    )
    INTERNATIONAL_FALSE_POSITIVE_TOKENS = (
        "国际新闻",
        "环球网",
        "参考消息",
        "海外网",
        "国际在线",
        "环球时报",
    )
    SPORTS_LOW_SIGNAL_TOKENS = (
        "赛事",
        "联赛",
        "杯赛",
        "赛季",
        "积分",
        "排行榜",
        "赛程",
    )
    # ---- 社会故事赛道（故事/人间观察类账号） ----
    #
    # 这个赛道和体育、国际一样需要自己的信号判据：热榜上真正属于
    # 「社会民生 / 家庭伦理 / 人情冷暖」的题只占少数，其余是体育、时政、
    # 娱乐和商业新闻。没有判据就会把「男篮输球」这类完全跑题的题放进选题池。
    #
    # SOCIAL_STORY_SIGNAL_TOKENS 判「这条题有没有人的生活处境」，
    # SOCIAL_STORY_LOW_SIGNAL_TOKENS 判「这条题明显不属于本赛道」。
    SOCIAL_STORY_SIGNAL_TOKENS = (
        # 人与关系
        "老人",
        "老太",
        "大爷",
        "大妈",
        "夫妻",
        "妻子",
        "丈夫",
        "儿子",
        "女儿",
        "儿媳",
        "女婿",
        "婆婆",
        "岳母",
        "父母",
        "孩子",
        "邻居",
        "房东",
        "租客",
        "同事",
        "老板",
        "员工",
        "农民工",
        "保安",
        "外卖员",
        "快递员",
        "保洁",
        "司机",
        # 处境与冲突
        "赡养",
        "养老",
        "退休",
        "遗产",
        "房产",
        "彩礼",
        "离婚",
        "再婚",
        "相亲",
        "官司",
        "判决",
        "赔偿",
        "调解",
        "欠薪",
        "讨薪",
        "借钱",
        "诈骗",
        "骗局",
        "摔倒",
        "扶不扶",
        "让座",
        "噪音",
        "扰民",
        "物业",
        "婆媳",
        "啃老",
        "带娃",
        "隔代",
        # 事故/纠纷/赔付 —— 这类题常不带人物称谓（「9万买的马车祸身亡 主人获赔13.5万」），
        # 但同样是标准的民生社会新闻，只靠关系词会漏掉。
        # 注意不要放单字「撞」：它会把「菲船故意冲撞中方」这种时政题也算成社会新闻。
        "车祸",
        "身亡",
        "受伤",
        "获赔",
        "赔付",
        "理赔",
        "追责",
        "判刑",
        "肇事",
        "撞人",
        "被撞",
    )
    SOCIAL_STORY_LOW_SIGNAL_TOKENS = (
        # 体育
        "男篮",
        "男足",
        "国足",
        "女篮",
        "女排",
        "中超",
        "英超",
        "西甲",
        "NBA",
        "CBA",
        "欧冠",
        "世界杯",
        "奥运",
        "联赛",
        "赛季",
        "夺冠",
        "晋级",
        "比分",
        # 时政/军事/外交
        "外交部",
        "国防部",
        "军演",
        "军舰",
        "战机",
        "导弹",
        "关税",
        "制裁",
        "峰会",
        "会谈",
        "访华",
        "白宫",
        "北约",
        "停火",
        "冲突升级",
        # 娱乐/明星
        "恋情",
        "分手",
        "结婚照",
        "演唱会",
        "新歌",
        "综艺",
        "票房",
        "收视",
        # 资本市场/企业财报（属于科技财经赛道，不是社会故事）
        "财报",
        "营收",
        "净利润",
        "股价",
        "涨停",
        "跌停",
        "融资",
        "IPO",
        "招股",
        "市值",
    )
    INTERNATIONAL_SIGNAL_TOKENS = (
        "美国",
        "俄罗斯",
        "乌克兰",
        "中东",
        "欧盟",
        "北约",
        "日本",
        "韩国",
        "英国",
        "法国",
        "德国",
        "印度",
        "伊朗",
        "朝鲜",
        "以色列",
        "巴勒斯坦",
        "叙利亚",
        "阿富汗",
        "土耳其",
        "沙特",
        "巴西",
        "澳大利亚",
        "加拿大",
        "墨西哥",
        "东南亚",
    )
    INTERNATIONAL_CONTEXT_FALSE_POSITIVE_TOKENS = (
        "国际关系",
        "国际法",
        "国际组织",
        "国际会议",
        "国际合作与交流",
        "国际社会",
        "海外",
        "外交部",
    )
    SPORTS_EVENT_TOKENS = (
        "足球",
        "篮球",
        "网球",
        "乒乓球",
        "排球",
        "游泳",
        "田径",
        "拳击",
        "高尔夫",
        "滑雪",
        "赛车",
        "羽毛球",
        "体操",
        "跳水",
        "举重",
        "击剑",
        "射击",
        "摔跤",
        "柔道",
        "跆拳道",
        "马术",
        "帆船",
        "橄榄球",
        "棒球",
        "冰球",
        "短道",
        "花滑",
        "NBA",
        "CBA",
        "世界杯",
        "奥运会",
        "亚运会",
        "世锦赛",
        "全运会",
        "transfer",
        "Premier League",
        "World Cup",
        "Wimbledon",
        "French Open",
        "Grand Slam",
        "squad",
        "manager",
        "champion",
        "football",
        "tennis",
    )
    SPORTS_CONTEXT_FALSE_POSITIVE_TOKENS = (
        "体育产业",
        "体育总局",
        "体育赛事",
        "体育频道",
        "体育场馆",
        "体育协会",
        "体育营销",
        "体育赞助",
        "体育彩票",
        "体育文化",
        "健身",
        "体育产业发展",
        "体育教育",
    )
    TOPIC_TYPE_LABELS = {
        "deep_analysis": "深度分析",
        "quick_commentary": "快讯点评",
        "project_breakdown": "项目拆解",
        "data_recap": "数据复盘",
        "industry_event": "行业事件",
        "project_progress": "项目进展",
        "financing_update": "融资动态",
        "earnings_signal": "业绩信号",
        "policy_market": "政策与市场",
        "product_launch": "产品发布",
    }
    PROJECT_TOKENS = ("项目", "中标", "签约", "并网", "开工", "投产", "示范", "验收", "交付", "装机")
    FINANCE_TOKENS = ("融资", "募资", "pre-a", "a轮", "b轮", "c轮", "天使轮", "种子轮", "投资")
    EARNINGS_TOKENS = ("财报", "业绩", "营收", "净利", "亏损", "毛利", "一季报", "半年报", "年报", "q1", "q2", "q3", "q4")
    POLICY_TOKENS = ("政策", "意见", "通知", "规划", "补贴", "新规", "办法", "指引")
    PRODUCT_TOKENS = ("发布", "推出", "首发", "问世", "新品", "新机", "方案", "平台")
    DATA_RECAP_TOKENS = ("统计数据表", "统计公报", "产量统计", "销量统计", "装机量", "出货量", "价格指数", "均价", "统计数据")
    EXECUTION_TOKENS = ("订单", "客户", "交付", "量产", "中标", "签约", "投产", "并网", "装机", "验收", "出货")
    CONFLICT_TOKENS = (
        "不是",
        "而是",
        "反攻",
        "逆袭",
        "替代",
        "挤压",
        "衰退",
        "下滑",
        "洗牌",
        "分化",
        "重估",
        "突围",
        "改写",
        "转向",
        "转移",
        "迁移",
        "抢单",
        "价格战",
        "撕开",
    )
    SCENE_TOKENS = (
        "机场",
        "电厂",
        "电站",
        "港口",
        "车间",
        "仓库",
        "园区",
        "工厂",
        "基地",
        "码头",
        "矿区",
        "变电站",
        "物流中心",
        "示范项目",
        "生产线",
        "机场",
    )
    CROSS_BORDER_TOKENS = (
        "中国",
        "日本",
        "美国",
        "欧洲",
        "德国",
        "东京",
        "海外",
        "出海",
        "国际",
        "日韩",
        "东南亚",
    )
    BENEFIT_SHIFT_TOKENS = (
        "利润",
        "毛利",
        "份额",
        "议价",
        "红利",
        "系统集成",
        "零部件",
        "整机",
        "运维",
        "服务",
        "回款",
        "IRR",
    )
    WEAK_EVENT_TOKENS = ("大会", "论坛", "峰会", "收官", "回顾", "印发", "规划纲要", "发布会", "启动仪式")
    NOTICE_STYLE_TOKENS = (
        "中标",
        "候选人公示",
        "中标候选人",
        "中标结果",
        "招标公告",
        "采购公告",
        "询价公告",
        "竞争性谈判",
        "比选公告",
        "开标",
        "成交公告",
        "结果公示",
        "发布会",
        "论坛",
        "峰会",
        "大会",
        "回顾",
        "成功举办",
        "启幕",
        "举行",
    )
    NOTICE_STYLE_RE = re.compile(
        r"^(?:中标|招标|成交|开标|公示|公告|回顾|发布会|论坛|峰会|大会)[\\s|｜:：\\-]"
    )
    PRICE_HEADLINE_RE = re.compile(
        r"\\d+(?:\\.\\d+)?\\s*(?:元/Wh|元/W|万元|亿元|MW|GW|GWh|MWh)"
    )
    COMPANY_ORG_RE = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:科技|能源|汽车|股份|集团|电气|智能|机器人|半导体|电子|时代|精工|制造|材料|电池|重工|工业|光伏|储能|动力|机电|控股|装备|技术)"
    )
    SCENE_ANCHOR_RE = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{0,10}(?:机场|电厂|电站|港口|车间|仓库|园区|工厂|基地|码头|矿区|变电站|物流中心|示范项目|生产线)"
    )
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    HTML_CHARSET_PATTERN = re.compile(br"charset\s*=\s*[\"']?([a-zA-Z0-9_\-]+)", re.I)
    XML_ENCODING_PATTERN = re.compile(br"<\?xml[^>]+encoding=[\"']([a-zA-Z0-9_\-]+)[\"']", re.I)
    MOJIBAKE_MARKERS = ("å", "æ", "ç", "é", "ï", "¤", "�")
    MOJIBAKE_CJK_MARKERS = (
        "鍥", "鏂", "鐢", "鑳", "缃", "鍙", "璁", "闂", "绔", "鍌", "姹", "鍑", "閿", "浠", "璧",
        # GBK-as-UTF-8 常见乱码字符（来自36氪等RSS源）
        "浜", "紒", "鏁", "瀹", "鐙", "鍏", "借", "鏇", "濇", "柊", "铻", "嶈", "祫", "锛",
        "屽", "鍊", "兼", "垨", "瓒", "浜", "鍖", "哄", "尯", "鍒", "╁", "彲", "鑳", "鎹",
    )
    # 类常量定义
    MAX_NUMBERS_EXTRACTED = 6
    MAX_SUMMARY_LENGTH = 120
    MIN_FRESHNESS_SCORE = 35
    FRESHNESS_6H = 98
    FRESHNESS_12H = 95
    FRESHNESS_24H = 88
    FRESHNESS_48H = 70
    FRESHNESS_72H = 58
    FRESHNESS_OVER_72H = 42
    DEFAULT_RELAXED_MAX_AGE_HOURS = 72
    MAX_TOPIC_AGE_HOURS_FALLBACK = 24
    MIN_SCORE_BOUND = 18
    MAX_SCORE_BOUND = 98
    MAX_HIGH_READ_SCORE = 72
    MAX_HIGH_SAVE_SCORE = 72
    MAX_HIGH_DISCUSSION_SCORE = 72
    LOW_SIGNAL_TEXT_LENGTH = 12
    MAX_SNIPPET_LENGTH_FOR_UNUSABLE_CHECK_1 = 140
    MAX_SNIPPET_LENGTH_FOR_UNUSABLE_CHECK_2 = 120
    ARTICLE_CONTEXT_MAX_LENGTH = 1800
    ARTICLE_CONTEXT_MIN_LENGTH = 220
    TOPIC_PREVIEW_CONCURRENCY = 4
    RSS_PREVIEW_MIN_LENGTH = 180
    MIN_KEYWORD_HITS_FOR_RELAXED = 2
    MIN_TITLE_HITS_FOR_RELAXED = 1
    MAX_EXPORT_TOPICS_FALLBACK = 6

    LOW_SIGNAL_TOPIC_TEXT_RE = re.compile(
        r"^(?:"
        r"\u521a\u521a|"
        r"\u6700\u65b0(?:\u52a8\u6001)?|"
        r"\d+\s*(?:\u5206\u949f\u524d|\u5c0f\u65f6\u524d|\u5929\u524d)|"
        r"(?:20\d{2}[./-](?:0?[1-9]|1[0-2])[./-](?:0?[1-9]|[12]\d|3[01]))|"
        r"(?:(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01]))"
        r")(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$"
    )
    META_DESCRIPTION_PATTERNS = (
        re.compile(r'<meta[^>]+(?:name|property)=["\']description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
        re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']description["\']', re.I),
        re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
        re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', re.I),
    )
    ARTICLE_PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.topic_settings: TopicCollectionSettings = settings.topic_collection
        self._ssl_context = ssl._create_unverified_context()
        self.growth_ops_service = GrowthOpsService(settings, logger)

    def collect_sync(self) -> DailyTopicsReport:
        return asyncio.run(self.collect())

    async def collect(self) -> DailyTopicsReport:
        collectors = self._build_collectors()
        results = await asyncio.gather(*(collector.collect() for collector in collectors))

        raw_items: list[TopicCandidate] = []
        source_stats: dict[str, dict[str, int | str]] = {}
        failed_sources: list[dict[str, str]] = []
        filtered_out = {
            "missing_timestamp": 0,
            "stale": 0,
            "industry_mismatch": 0,
            "weak_focus": 0,
            "low_quality": 0,
            "duplicate": 0,
            "source_overflow": 0,
            "relaxed_added": 0,
        }

        for result in results:
            raw_items.extend(result.items)
            source_stats[result.source_name] = {
                "raw_count": len(result.items),
                "status": "ok" if not result.errors else "partial_failure",
            }
            for error in result.errors:
                failed_sources.append({"source": result.source_name, "error": error})

        scored = [self._enrich_topic(item) for item in raw_items]
        freshness_filtered = self._filter_by_freshness(scored, filtered_out)
        filtered = self._filter_to_focus_industries(freshness_filtered, filtered_out)
        filtered = [item for item in filtered if self._passes_topic_focus(item, filtered_out)]
        deduped = self._dedupe_topics(filtered, filtered_out)
        ranked = self._rank_topics(deduped)
        capped = self._apply_source_cap(ranked, filtered_out)[: self.topic_settings.max_export_topics]
        capped = self._supplement_topics_if_needed(capped, scored, filtered_out)
        capped = await self._hydrate_selected_topic_previews(capped)
        capped = self._rank_topics(capped)[: self.topic_settings.max_export_topics]

        for index, item in enumerate(capped, start=1):
            item.rank = index

        return DailyTopicsReport(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            lookback_days=self.topic_settings.lookback_days,
            max_topic_age_hours=self.topic_settings.max_topic_age_hours,
            focus_industries=self.topic_settings.focus_industries,
            total_raw_items=len(raw_items),
            total_selected_items=len(capped),
            topics=capped,
            source_stats=source_stats,
            failed_sources=failed_sources,
            filtered_out=filtered_out,
        )

    def _build_collectors(self) -> list["_BaseTopicCollector"]:
        collectors: list[_BaseTopicCollector] = []
        source_configs = self.topic_settings.source_configs
        for source_name in self.topic_settings.enabled_sources:
            source_config = dict(source_configs.get(source_name, {}))
            if not source_config:
                continue

            source_type = str(source_config.get("type", "html"))
            collector_name = str(source_config.get("collector_name", source_name)).strip() or source_name
            if source_type == "hot_board":
                collectors.append(_HotBoardTopicCollector(source_name, source_config, self))
            elif source_type == "baidu_hot":
                collectors.append(_BaiduHotTopicCollector(source_name, source_config, self))
            elif source_type in {"rss", "google_rss"}:
                collectors.append(_RssTopicCollector(source_name, source_config, self, google_mode=source_type == "google_rss"))
            else:
                same_domain_only = bool(source_config.get("same_domain_only", collector_name == "gg_robot"))
                if collector_name == "zhidx":
                    collectors.append(_ZhidxTopicCollector(source_name, source_config, self))
                elif collector_name == "ofweek":
                    collectors.append(_OfweekTopicCollector(source_name, source_config, self))
                else:
                    collectors.append(
                        _GenericHtmlTopicCollector(
                            source_name,
                            source_config,
                            self,
                            same_domain_only=same_domain_only,
                        )
                    )
        return collectors

    async def fetch_text(self, url: str, encoding: str | None = None) -> str:
        return await asyncio.to_thread(self._fetch_text_sync, url, encoding)

    def _fetch_text_sync(self, url: str, encoding: str | None = None) -> str:
        # 快速失败：域名黑名单
        domain = urlparse(url).hostname or ""
        if domain in self.URL_DOMAIN_BLACKLIST:
            self.logger.info("Skipping blacklisted domain: %s", domain)
            raise HTTPError(url, 403, "Domain blacklisted", {}, None)

        retry_delays = (0.0, 1.2, 3.0)
        configured_timeout = max(8, int(self.topic_settings.request_timeout_seconds))
        # In GitHub Actions, fail fast on flaky sources so one slow site does not stall the whole run.
        request_timeout = min(15, configured_timeout) if os.getenv("GITHUB_ACTIONS") else configured_timeout
        last_error: Exception | None = None
        for attempt, delay in enumerate(retry_delays, start=1):
            if delay > 0:
                time.sleep(delay)
            request = Request(
                url,
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            try:
                with urlopen(
                    request,
                    context=self._ssl_context,
                    timeout=request_timeout,
                ) as response:
                    raw = response.read()
                    charset = encoding or response.headers.get_content_charset()
                    return self._decode_html_payload(raw, charset)
            except HTTPError as exc:
                last_error = exc
                if exc.code in {400, 401, 403, 404, 410} or attempt == len(retry_delays):
                    raise
                self.logger.warning("Retrying topic fetch after HTTP %s from %s", exc.code, url)
            except Exception as exc:
                last_error = exc
                if attempt == len(retry_delays):
                    raise
                self.logger.warning("Retrying topic fetch after error from %s: %s", url, exc)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to fetch topic source: {url}")

    def _decode_html_payload(self, raw: bytes, preferred_charset: str | None = None) -> str:
        candidates: list[str] = []
        xml_charset = self._extract_xml_encoding(raw)
        meta_charset = self._extract_meta_charset(raw)
        if xml_charset:
            try:
                decoded = raw.decode(xml_charset, "ignore")
            except LookupError:
                decoded = ""
            if decoded and not self._looks_like_mojibake(decoded):
                return decoded
        for candidate in (preferred_charset, meta_charset, "utf-8", "utf-8-sig", "gb18030", "gbk", "big5"):
            normalized = str(candidate or "").strip().lower()
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        best_text = ""
        best_score = float("-inf")
        for candidate in candidates:
            try:
                decoded = raw.decode(candidate, "ignore")
            except LookupError:
                continue
            score = self._decoded_text_score(decoded)
            if score > best_score:
                best_score = score
                best_text = decoded
        return best_text or raw.decode("utf-8", "ignore")

    def _extract_meta_charset(self, raw: bytes) -> str:
        match = self.HTML_CHARSET_PATTERN.search(raw[:2048])
        if not match:
            return ""
        try:
            return match.group(1).decode("ascii", "ignore").strip()
        except Exception:
            return ""

    def _extract_xml_encoding(self, raw: bytes) -> str:
        match = self.XML_ENCODING_PATTERN.search(raw[:256])
        if not match:
            return ""
        try:
            return match.group(1).decode("ascii", "ignore").strip()
        except Exception:
            return ""

    def _decoded_text_score(self, text: str) -> float:
        if not text:
            return float("-inf")
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        ascii_count = len(re.findall(r"[A-Za-z]", text))
        mojibake_count = sum(text.count(marker) for marker in self.MOJIBAKE_MARKERS)
        mojibake_cjk_count = sum(text.count(marker) for marker in self.MOJIBAKE_CJK_MARKERS)
        replacement_count = text.count("\ufffd")
        readable_hits = sum(text.count(keyword) for keyword in self._decoding_hint_keywords())
        return (
            cjk_count * 1.5
            + min(ascii_count, 300) * 0.05
            + readable_hits * 18
            - mojibake_count * 4
            - mojibake_cjk_count * 3
            - replacement_count * 6
        )

    def _looks_like_mojibake(self, text: str) -> bool:
        if not text:
            return False
        mojibake_count = sum(text.count(marker) for marker in self.MOJIBAKE_MARKERS)
        mojibake_cjk_count = sum(text.count(marker) for marker in self.MOJIBAKE_CJK_MARKERS)
        return (mojibake_count + mojibake_cjk_count) >= 2

    def _decoding_hint_keywords(self) -> list[str]:
        keywords = {
            "储能",
            "新能源",
            "机器人",
            "行业",
            "产业",
            "项目",
            "公司",
            "技术",
            "市场",
            "电池",
            "光伏",
            "风电",
            "电力",
        }
        for items in self.topic_settings.keyword_map.values():
            keywords.update(keyword for keyword in items if keyword)
        return list(keywords)

    def _enrich_topic(self, topic: TopicCandidate) -> TopicCandidate:
        combined = f"{topic.title}\n{topic.snippet}\n{topic.summary}"
        matched_industries, matched_keywords = self._match_industries(combined)
        # 实时热榜话题天然归属「热点」赛道：无论是否命中行业关键词，
        # 只要账号启用了热点赛道即视为聚焦热点内容。
        #
        # ⚠️ 2026-09-18：这里原来**无条件**打上 "hot_topic"。但社会故事类账号的
        # `focus_industries` 里必须去掉 hot_topic（理由见 `_passes_topic_focus`），
        # 于是热榜题被打上 "hot_topic"、而账号只认 "social_story"，
        # `_filter_to_focus_industries` 的「matched_industries 与 focus 有交集」
        # 判定失败 —— 实测 54 条热榜题全部被计入 industry_mismatch，选题池为 0，
        # 连「男子高空作业绳子被2楼老人剪断」这种正对题的题也进不来。
        # 所以自动归属要跟随账号**实际启用的**热点赛道，而不是写死 hot_topic。
        if topic.source_type in self.HOT_SOURCE_TYPES:
            hot_tracks = [
                track
                for track in ("hot_topic", "social_story")
                if track in set(self.topic_settings.focus_industries)
            ]
            for track in hot_tracks or ["hot_topic"]:
                if track not in matched_industries:
                    matched_industries = [*matched_industries, track]
        numbers = self._extract_numbers(combined)
        freshness_score = self._freshness_score(topic.published_at)
        topic.matched_industries = matched_industries
        topic.matched_keywords = matched_keywords
        topic.extracted_numbers = numbers
        topic.summary = self._summarize_topic(topic)
        topic.read_score = self._score_read(topic, freshness_score)
        topic.save_score = self._score_save(topic, freshness_score)
        topic.discussion_score = self._score_discussion(topic, freshness_score)
        topic.heat_score = self._score_heat(topic, freshness_score)
        topic.topic_type = self._classify_topic_type(topic)
        topic.topic_type_label = self.TOPIC_TYPE_LABELS.get(topic.topic_type, topic.topic_type or "行业事件")
        topic.evidence_strength = self._score_evidence_strength(topic, freshness_score)
        topic.quality_flags = self._topic_quality_flags(topic, freshness_score, topic.evidence_strength)
        topic.recommended_format, topic.recommended_format_label = self._recommended_format_for_topic(
            topic,
            topic.topic_type,
            topic.evidence_strength,
        )
        topic.quality_score = self._score_topic_quality(topic, freshness_score)
        title_potential = self.growth_ops_service.score_topic_title_potential(
            title=topic.title,
            summary=topic.summary,
            snippet=topic.snippet,
            matched_keywords=topic.matched_keywords,
            extracted_numbers=topic.extracted_numbers,
            matched_industries=topic.matched_industries,
        )
        topic.title_potential_score = int(title_potential.get("score", 0))
        topic.title_playbook_track = str(title_potential.get("track", "")).strip()
        topic.title_potential_reasons = [
            str(item).strip() for item in title_potential.get("reasons", []) if str(item).strip()
        ]
        base_total = round(
            topic.heat_score * 0.35
            + topic.read_score * 0.2
            + topic.save_score * 0.25
            + topic.discussion_score * 0.2
        )
        topic.total_score = round(
            base_total * 0.55
            + topic.quality_score * 0.25
            + topic.title_potential_score * 0.20
        )
        # 热榜真实热点：平台热度是浏览量的最强先验，直接加权进总分
        # （标题型热点没有证据分/收藏分优势，若不加权会被行业深度稿压到后面）
        if topic.source_type in self.HOT_SOURCE_TYPES and topic.heat_value > 0:
            import math as _math
            heat_priority = min(34, max(4, int(_math.log10(max(topic.heat_value, 1)) * 4)))
            topic.total_score = min(self.MAX_SCORE_BOUND, topic.total_score + heat_priority)
        topic.total_score = max(1, topic.total_score - self._selection_penalty(topic))
        topic.is_high_read = topic.read_score >= self.MAX_HIGH_READ_SCORE
        topic.is_high_save = topic.save_score >= self.MAX_HIGH_SAVE_SCORE
        topic.is_high_discussion = topic.discussion_score >= self.MAX_HIGH_DISCUSSION_SCORE
        return topic

    def _match_industries(self, text: str) -> tuple[list[str], list[str]]:
        lowered = text.lower()
        matched_industries: list[str] = []
        matched_keywords: list[str] = []
        for industry, keywords in self.topic_settings.keyword_map.items():
            current_keywords = [keyword for keyword in keywords if keyword.lower() in lowered]
            if current_keywords:
                matched_industries.append(industry)
                matched_keywords.extend(current_keywords)
        return matched_industries, self._unique(matched_keywords)

    def _extract_numbers(self, text: str) -> list[str]:
        return self._unique(self.NUMBER_PATTERN.findall(text))[:self.MAX_NUMBERS_EXTRACTED]

    def _freshness_score(self, published_at: str) -> int:
        if not published_at:
            return self.MIN_FRESHNESS_SCORE
        published = self._parse_datetime(published_at)
        if published is None:
            return self.MIN_FRESHNESS_SCORE
        now = datetime.now(timezone.utc)
        delta = now - published.astimezone(timezone.utc)
        hours = max(delta.total_seconds() / 3600, 0)
        if hours <= 6:
            return self.FRESHNESS_6H
        if hours <= 12:
            return self.FRESHNESS_12H
        if hours <= 24:
            return self.FRESHNESS_24H
        if hours <= 48:
            return self.FRESHNESS_48H
        if hours <= 72:
            return self.FRESHNESS_72H
        return self.FRESHNESS_OVER_72H

    def _parse_datetime(self, value: str) -> datetime | None:
        value = value.strip()
        if not value:
            return None
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        now_local = datetime.now(local_tz)

        if value in {"刚刚", "最新", "最新动态"}:
            return now_local
        relative_match = re.search(r"(\d+)\s*(分钟|小时|天)前", value)
        if relative_match:
            amount = int(relative_match.group(1))
            unit = relative_match.group(2)
            delta = timedelta()
            if unit == "分钟":
                delta = timedelta(minutes=amount)
            elif unit == "小时":
                delta = timedelta(hours=amount)
            elif unit == "天":
                delta = timedelta(days=amount)
            return now_local - delta
        if value.startswith("今天"):
            return now_local
        if value.startswith("昨天"):
            return now_local - timedelta(days=1)
        if value.startswith("前天"):
            return now_local - timedelta(days=2)
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass

        date_only_patterns = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
        datetime_patterns = ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M:%S")

        for pattern in datetime_patterns:
            try:
                return datetime.strptime(value[:19], pattern).replace(tzinfo=local_tz)
            except ValueError:
                continue

        chinese_datetime_patterns = (
            "%Y年%m月%d日 %H:%M:%S",
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日",
        )
        for pattern in chinese_datetime_patterns:
            try:
                parsed = datetime.strptime(value, pattern)
                if pattern.endswith("日"):
                    parsed = parsed.replace(
                        hour=now_local.hour,
                        minute=now_local.minute,
                        second=0,
                        microsecond=0,
                    )
                return parsed.replace(tzinfo=local_tz)
            except ValueError:
                continue

        for pattern in date_only_patterns:
            try:
                parsed = datetime.strptime(value[:10], pattern)
                return parsed.replace(
                    hour=now_local.hour,
                    minute=now_local.minute,
                    second=0,
                    microsecond=0,
                    tzinfo=local_tz,
                )
            except ValueError:
                continue

        chinese_month_day_match = re.search(
            r"(?<!\d)(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
            value,
        )
        if chinese_month_day_match:
            month = int(chinese_month_day_match.group(1))
            day = int(chinese_month_day_match.group(2))
            now_local = datetime.now(local_tz)
            hour = int(chinese_month_day_match.group(3) or now_local.hour)
            minute = int(chinese_month_day_match.group(4) or 0)
            second = int(chinese_month_day_match.group(5) or 0)
            try:
                parsed = datetime(now_local.year, month, day, hour, minute, second, tzinfo=local_tz)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed > now_local + timedelta(days=1):
                    parsed = parsed.replace(year=parsed.year - 1)
                return parsed

        month_day_match = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?!\d)", value)
        if month_day_match:
            month, day = (int(part) for part in month_day_match.groups())
            now_local = datetime.now(local_tz)
            try:
                parsed = datetime(
                    now_local.year,
                    month,
                    day,
                    now_local.hour,
                    now_local.minute,
                    0,
                    tzinfo=local_tz,
                )
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed > now_local + timedelta(days=1):
                    parsed = parsed.replace(year=parsed.year - 1)
                return parsed

        match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                now_local = datetime.now(local_tz)
                return datetime(
                    year,
                    month,
                    day,
                    now_local.hour,
                    now_local.minute,
                    0,
                    tzinfo=local_tz,
                )
            except ValueError:
                return None
        return None

    def _filter_by_freshness(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        age_limits = [
            value
            for value in (
                self.topic_settings.max_topic_age_hours,
                self.topic_settings.lookback_days * 24,
            )
            if value and value > 0
        ]
        max_age_hours = min(age_limits) if age_limits else self.MAX_TOPIC_AGE_HOURS_FALLBACK
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        keep: list[TopicCandidate] = []

        for topic in topics:
            published = self._parse_datetime(topic.published_at)
            if published is None:
                if self.topic_settings.drop_topics_without_timestamp:
                    filtered_out["missing_timestamp"] += 1
                    continue
                keep.append(topic)
                continue

            published_utc = published.astimezone(timezone.utc)
            if published_utc < cutoff:
                filtered_out["stale"] += 1
                continue
            keep.append(topic)

        return keep

    # 泛关键词黑名单 - 这些词太宽泛，不应单独作为匹配依据
    GENERIC_KEYWORD_BLACKLIST = frozenset({
        "AI", "公司", "企业", "科技", "技术", "模型", "发展", "市场",
        "产业", "产业链", "供应链", "行业", "项目", "投资", "合作", "建设",
        "生产", "制造", "热点", "热搜", "热议", "爆火", "刷屏", "官宣",
        "突发", "通报", "回应", "反转",
    })
    GENERIC_KEYWORD_BLACKLIST_LOWER = frozenset(
        keyword.casefold() for keyword in GENERIC_KEYWORD_BLACKLIST
    )

    def _has_concrete_industry_keyword(self, topic: TopicCandidate) -> bool:
        return any(
            str(keyword).strip().casefold() not in self.GENERIC_KEYWORD_BLACKLIST_LOWER
            for keyword in (topic.matched_keywords or [])
            if str(keyword).strip()
        )

    def _filter_to_focus_industries(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        matched_topics = [item for item in topics if item.matched_industries]
        filtered_out["industry_mismatch"] += max(len(topics) - len(matched_topics), 0)
        if not matched_topics:
            return []

        focus_set = set(self.topic_settings.focus_industries)
        focused_topics = [
            item
            for item in matched_topics
            if any(industry in focus_set for industry in item.matched_industries)
        ]

        # Non-hot sources need at least one concrete industry keyword.  A
        # previous fallback returned focused_topics/matched_topics when every
        # match was generic, which let "AI" and "公司" promote unrelated
        # articles into all five technology industries.
        candidates = focused_topics if focus_set else matched_topics
        specific_filtered = []
        for item in candidates:
            if item.source_type in self.HOT_SOURCE_TYPES:
                specific_filtered.append(item)
                continue
            if self._has_concrete_industry_keyword(item):
                specific_filtered.append(item)
            else:
                filtered_out["industry_mismatch"] += 1

        if focus_set:
            filtered_out["industry_mismatch"] += max(len(matched_topics) - len(focused_topics), 0)
        return specific_filtered

    def _score_heat(self, topic: TopicCandidate, freshness_score: int) -> int:
        pattern_score = self._high_performance_pattern_score(topic)
        score = 42 + len(topic.matched_industries) * 12 + len(topic.matched_keywords[:4]) * 4 + freshness_score // 5
        if topic.source == "google_news":
            score += 6
        if topic.source == "36kr":
            score += 5
        if topic.extracted_numbers:
            score += 7
        score += self._focus_bonus(topic)
        score += min(10, pattern_score // 2)
        if self._is_weak_event_topic(topic):
            score -= 10
        score -= self._macro_noise_penalty(topic)
        # 热榜来源直接用平台热度值归一化：热度越高，heat_score 越高
        if topic.source_type in self.HOT_SOURCE_TYPES and topic.heat_value > 0:
            heat_bonus = int(min(40, max(8, (topic.heat_value ** 0.22) / 10)))
            score += heat_bonus
        return min(self.MAX_SCORE_BOUND, score)

    def _score_read(self, topic: TopicCandidate, freshness_score: int) -> int:
        pattern_score = self._high_performance_pattern_score(topic)
        score = 45 + freshness_score // 4
        if 12 <= len(topic.title) <= 28:
            score += 10
        if any(token in topic.title for token in self.READ_TOKENS):
            score += 10
        if topic.matched_keywords:
            score += 8
        if self._has_scene_anchor(topic):
            score += 8
        if self._has_conflict_signal(topic):
            score += 6
        score += min(8, pattern_score // 3)
        if self._is_weak_event_topic(topic):
            score -= 12
        score += self._focus_bonus(topic)
        score -= self._macro_noise_penalty(topic)
        return min(self.MAX_SCORE_BOUND - 2, score)

    def _score_save(self, topic: TopicCandidate, freshness_score: int) -> int:
        pattern_score = self._high_performance_pattern_score(topic)
        score = 38 + freshness_score // 6 + len(topic.extracted_numbers) * 8
        if len(topic.matched_keywords) >= 3:
            score += 12
        if any(token in f"{topic.title} {topic.summary}" for token in self.SAVE_TOKENS):
            score += 10
        if self._has_benefit_shift_signal(topic):
            score += 10
        if self._has_scene_anchor(topic):
            score += 6
        score += min(8, pattern_score // 4)
        if self._is_weak_event_topic(topic):
            score -= 8
        score += self._focus_bonus(topic) // 2
        score -= self._macro_noise_penalty(topic) // 2
        return min(self.MAX_SCORE_BOUND - 2, score)

    def _score_discussion(self, topic: TopicCandidate, freshness_score: int) -> int:
        text = f"{topic.title} {topic.summary}"
        pattern_score = self._high_performance_pattern_score(topic)
        score = 35 + freshness_score // 7
        if any(token in text for token in self.DISCUSSION_TOKENS):
            score += 16
        if len(topic.matched_industries) > 1:
            score += 10
        if "?" in topic.title or "？" in topic.title:
            score += 8
        if self._has_conflict_signal(topic):
            score += 10
        if self._has_cross_border_signal(topic):
            score += 8
        score += min(10, pattern_score // 3)
        if self._is_weak_event_topic(topic):
            score -= 10
        score += self._focus_bonus(topic) // 2
        score -= self._macro_noise_penalty(topic)
        return min(self.MAX_SCORE_BOUND - 2, score)

    def _classify_topic_type(self, topic: TopicCandidate) -> str:
        text = f"{topic.title} {topic.summary} {topic.snippet}".lower()
        if any(token.lower() in text for token in self.DATA_RECAP_TOKENS):
            return "data_recap"
        if any(token.lower() in text for token in self.POLICY_TOKENS):
            return "policy_market"
        if any(token.lower() in text for token in self.EARNINGS_TOKENS):
            return "earnings_signal"
        if any(token.lower() in text for token in self.FINANCE_TOKENS):
            return "financing_update"
        if any(token.lower() in text for token in self.PROJECT_TOKENS):
            return "project_progress"
        if any(token.lower() in text for token in self.PRODUCT_TOKENS):
            return "product_launch"
        return "industry_event"

    def _score_evidence_strength(self, topic: TopicCandidate, freshness_score: int) -> int:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        pattern_score = self._high_performance_pattern_score(topic)
        score = 22 + freshness_score // 8
        score += min(20, len(topic.extracted_numbers) * 6)
        score += min(14, len(topic.matched_keywords) * 3)
        if self._has_specific_subject(topic):
            score += 12
        if any(token in text for token in self.EXECUTION_TOKENS):
            score += 10
        if any(token in text for token in self.FINANCE_TOKENS):
            score += 6
        if any(token in text for token in self.EARNINGS_TOKENS):
            score += 8
        if topic.source in {"36kr", "zhidx", "ofweek"}:
            score += 4
        if self._has_scene_anchor(topic):
            score += 4
        if self._has_benefit_shift_signal(topic):
            score += 6
        score += min(8, pattern_score // 4)
        if self._is_title_only_topic(topic):
            score -= 12
        if self._is_old_statistics_topic(topic):
            score -= 18
        if self._is_weak_event_topic(topic):
            score -= 10
        score -= self._macro_noise_penalty(topic) // 2
        return max(self.MIN_SCORE_BOUND, min(self.MAX_SCORE_BOUND, score))

    def _topic_quality_flags(
        self,
        topic: TopicCandidate,
        freshness_score: int,
        evidence_strength: int,
    ) -> list[str]:
        flags: list[str] = []
        if self._macro_noise_penalty(topic) >= 12:
            flags.append("macro_noise")
        if self._is_title_only_topic(topic):
            flags.append("title_only")
        if not self._has_specific_subject(topic):
            flags.append("weak_subject")
        if evidence_strength < 48:
            flags.append("weak_evidence")
        if freshness_score < 58:
            flags.append("low_freshness")
        if self._is_old_statistics_topic(topic):
            flags.append("old_statistics")
        if self._is_weak_event_topic(topic):
            flags.append("weak_event")
        if self._is_notice_style_title(topic):
            flags.append("notice_style_title")
        if topic.topic_type == "data_recap" and len(topic.extracted_numbers) < 2:
            flags.append("weak_data_recap")
        return self._unique(flags)

    def _recommended_format_for_topic(
        self,
        topic: TopicCandidate,
        topic_type: str,
        evidence_strength: int,
    ) -> tuple[str, str]:
        flags = set(topic.quality_flags)
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        pattern_score = self._high_performance_pattern_score(topic)

        if "title_only" in flags and topic_type in {"policy_market", "industry_event", "product_launch"}:
            return "quick_commentary", "快讯点评"
        if "title_only" in flags and topic_type == "project_progress" and not any(
            token in text for token in self.EXECUTION_TOKENS
        ):
            return "quick_commentary", "快讯点评"
        if pattern_score >= 18 and evidence_strength >= 60 and self._has_specific_subject(topic):
            if topic_type in {"project_progress", "earnings_signal"} or self._has_scene_anchor(topic):
                return "project_breakdown", "项目拆解"
            return "deep_analysis", "深度分析"
        if topic_type == "data_recap":
            return "data_recap", "数据复盘"
        if topic_type == "policy_market":
            if evidence_strength >= 76 and "title_only" not in flags and self._has_specific_subject(topic):
                return "deep_analysis", "深度分析"
            return "quick_commentary", "快讯点评"
        if topic_type in {"project_progress", "earnings_signal"} and evidence_strength >= 58:
            return "project_breakdown", "项目拆解"
        if topic_type in {"financing_update", "policy_market", "product_launch"} and evidence_strength < 62:
            return "quick_commentary", "快讯点评"
        if evidence_strength >= 66:
            return "deep_analysis", "深度分析"
        return "quick_commentary", "快讯点评"

    def _score_topic_quality(self, topic: TopicCandidate, freshness_score: int) -> int:
        pattern_score = self._high_performance_pattern_score(topic)
        penalty = 0
        if "old_statistics" in topic.quality_flags:
            penalty += 18
        if "weak_evidence" in topic.quality_flags:
            penalty += 10
        if "weak_subject" in topic.quality_flags:
            penalty += 8
        if "title_only" in topic.quality_flags:
            penalty += 6
        if "macro_noise" in topic.quality_flags:
            penalty += 8
        if "weak_data_recap" in topic.quality_flags:
            penalty += 10
        if "weak_event" in topic.quality_flags:
            penalty += 14
        if "notice_style_title" in topic.quality_flags:
            penalty += 12

        score = round(
            topic.evidence_strength * 0.45
            + freshness_score * 0.15
            + topic.read_score * 0.15
            + topic.save_score * 0.15
            + topic.discussion_score * 0.10
            + pattern_score * 0.20
        )
        return max(18, min(98, score - penalty))

    def _summarize_topic(self, topic: TopicCandidate) -> str:
        cleaned_snippet = self._clean_text(topic.snippet)
        if cleaned_snippet and not self._is_unusable_topic_snippet(cleaned_snippet):
            sentence = re.split(r"[。！？!?；;\n]", cleaned_snippet)[0].strip()
            if sentence and not self._is_unusable_topic_snippet(sentence):
                return sentence[:self.MAX_SUMMARY_LENGTH]
        # ⚠️ 2026-09-18：社会故事赛道要单独出摘要文案。
        # 原来的兜底句是「{标题}，重点看{X}对应的项目落地和订单节奏」—— 这句会进入
        # 「素材摘要」，而 writer 是把它当**素材事实**读的，于是
        # 「男子高空作业绳子被2楼老人剪断」的摘要里写着「重点看老人对应的项目落地和订单节奏」，
        # 正文跟着就写成订单与毛利分析。领域不同，摘要措辞必须跟着换。
        social_story = "social_story" in {
            str(item).strip() for item in self.topic_settings.focus_industries
        }
        if social_story:
            if topic.matched_keywords:
                return (
                    f"{topic.title}，这件事的关键在于当事人的处境和后续处理结果，"
                    f"重点看{'、'.join(topic.matched_keywords[:2])}这条线上的实际情况。"
                )
            if topic.extracted_numbers:
                return f"{topic.title}，可核对的事实是{'、'.join(topic.extracted_numbers[:2])}。"
            return topic.title
        if topic.extracted_numbers and topic.matched_keywords:
            return (
                f"{topic.title}，当前可跟踪的信号包括"
                f"{'、'.join(topic.extracted_numbers[:2])}和{'、'.join(topic.matched_keywords[:2])}。"
            )
        if topic.matched_keywords:
            return f"{topic.title}，重点看{'、'.join(topic.matched_keywords[:2])}对应的项目落地和订单节奏。"
        if topic.extracted_numbers:
            return f"{topic.title}，关键数字包括{'、'.join(topic.extracted_numbers[:2])}。"
        return topic.title

    def _is_low_signal_topic_text(self, text: str) -> bool:
        cleaned = self._clean_text(text)
        if not cleaned:
            return True
        normalized = re.sub(r"\s+", "", cleaned)
        if self.LOW_SIGNAL_TOPIC_TEXT_RE.fullmatch(normalized):
            return True
        if len(normalized) <= self.LOW_SIGNAL_TEXT_LENGTH and re.fullmatch(r"[\d\-/:.年月日分小时前最新刚刚]+", normalized):
            return True
        return False

    def _looks_like_mojibake(self, text: str) -> bool:
        cleaned = str(text or "")
        if not cleaned:
            return False
        marker_count = sum(cleaned.count(marker) for marker in self.MOJIBAKE_CJK_MARKERS)
        if marker_count >= 6:
            return True
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
        readable_signal_count = sum(
            cleaned.count(token)
            for token in (
                "的",
                "了",
                "在",
                "和",
                "年",
                "月",
                "日",
                "项目",
                "公司",
                "市场",
                "储能",
                "新能源",
                "光伏",
                "风电",
                "电池",
                "并网",
                "订单",
            )
        )
        return cjk_count >= 10 and readable_signal_count == 0

    def _is_unusable_topic_snippet(self, text: str) -> bool:
        cleaned = self._clean_text(text)
        if not cleaned:
            return True
        if self._is_low_signal_topic_text(cleaned):
            return True
        if "<a " in cleaned.lower():
            return True
        lowered = cleaned.lower()
        if "copyright" in lowered or "all rights reserved" in lowered or "in-en.com" in lowered:
            return True
        if "»" in cleaned or "当前位置" in cleaned or cleaned.endswith("正文"):
            return True
        if self._looks_like_mojibake(cleaned):
            return True
        # Long RSS/meta descriptions are often the only source containing the
        # financial figures.  Keep them when they have a subject and several
        # report/data markers; length alone is not a reliable quality signal.
        if self._is_evidence_rich_topic_text(cleaned):
            return False
        if len(cleaned) > self.MAX_SNIPPET_LENGTH_FOR_UNUSABLE_CHECK_1 and len(re.findall(r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d+\s*(?:分钟前|小时前|天前))", cleaned)) >= 2:
            return True
        if len(cleaned) > self.MAX_SNIPPET_LENGTH_FOR_UNUSABLE_CHECK_2 and cleaned.count(" ") >= 6:
            return True
        if len(cleaned) > 140 and sum(cleaned.count(token) for token in ("，", "、", " | ", " / ")) >= 6:
            return True
        return False

    def _is_evidence_rich_topic_text(self, text: str) -> bool:
        candidate = self._clean_text(text)
        if not candidate:
            return False
        number_count = len(self.NUMBER_PATTERN.findall(candidate))
        evidence_tokens = (
            "财报",
            "半年报",
            "年报",
            "营业总收入",
            "营收",
            "归母净利润",
            "净利润",
            "经营现金流",
            "现金流",
            "每股收益",
            "净资产收益率",
            "订单",
            "交付",
            "中标",
            "并网",
            "量产",
        )
        evidence_count = sum(token in candidate for token in evidence_tokens)
        return number_count >= 2 and evidence_count >= 1

    def _dedupe_topics(self, topics: list[TopicCandidate], filtered_out: dict[str, int] | None = None) -> list[TopicCandidate]:
        result: list[TopicCandidate] = []
        seen_urls: set[str] = set()
        seen_titles: list[str] = []

        for item in sorted(topics, key=lambda current: current.total_score, reverse=True):
            normalized_url = self._normalize_url(item.url)
            normalized_title = self._normalize_title(item.title)
            if normalized_url and normalized_url in seen_urls:
                if filtered_out is not None:
                    filtered_out["duplicate"] += 1
                continue
            if any(self._title_similarity(normalized_title, existing) >= 0.9 for existing in seen_titles):
                if filtered_out is not None:
                    filtered_out["duplicate"] += 1
                continue

            if normalized_url:
                seen_urls.add(normalized_url)
            seen_titles.append(normalized_title)
            result.append(item)
        return result

    def _apply_source_cap(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        cap = max(self.topic_settings.max_ranked_items_per_source, 1)
        per_source: dict[str, int] = {}
        result: list[TopicCandidate] = []

        for topic in topics:
            current = per_source.get(topic.source, 0)
            if current >= cap:
                filtered_out["source_overflow"] += 1
                continue
            per_source[topic.source] = current + 1
            result.append(topic)
        return result

    def _rank_topics(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        return sorted(
            topics,
            key=lambda item: (
                item.total_score,
                item.quality_score,
                item.title_potential_score,
                self._high_performance_pattern_score(item),
                item.evidence_strength,
                self._freshness_score(item.published_at),
                item.save_score,
                item.discussion_score,
                item.read_score,
            ),
            reverse=True,
        )

    def _supplement_topics_if_needed(
        self,
        selected: list[TopicCandidate],
        scored: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        target_count = min(self.topic_settings.max_export_topics, self.MAX_EXPORT_TOPICS_FALLBACK)
        if len(selected) >= target_count:
            return selected

        relaxed_candidates = self._build_relaxed_candidates(scored)
        if not relaxed_candidates:
            return selected

        existing_urls = {self._normalize_url(item.url) for item in selected if item.url}
        existing_titles = {self._normalize_title(item.title) for item in selected if item.title}
        supplemented = list(selected)

        for topic in relaxed_candidates:
            normalized_url = self._normalize_url(topic.url)
            normalized_title = self._normalize_title(topic.title)
            if normalized_url and normalized_url in existing_urls:
                continue
            if normalized_title and normalized_title in existing_titles:
                continue
            if any(self._title_similarity(normalized_title, existing) >= 0.9 for existing in existing_titles if existing):
                continue
            supplemented.append(topic)
            filtered_out["relaxed_added"] += 1
            if normalized_url:
                existing_urls.add(normalized_url)
            if normalized_title:
                existing_titles.add(normalized_title)
            if len(supplemented) >= target_count:
                break

        return supplemented[: self.topic_settings.max_export_topics]

    def _build_relaxed_candidates(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        focus_set = set(self.topic_settings.focus_industries)
        relaxed_cutoff = datetime.now(timezone.utc) - timedelta(hours=max(self.topic_settings.max_topic_age_hours, self.DEFAULT_RELAXED_MAX_AGE_HOURS))
        pool: list[TopicCandidate] = []

        for topic in topics:
            if not topic.matched_industries:
                continue
            if focus_set and not any(industry in focus_set for industry in topic.matched_industries):
                continue
            if topic.source_type not in self.HOT_SOURCE_TYPES and not self._has_concrete_industry_keyword(topic):
                continue
            if self._has_blocking_quality_issue(topic):
                continue
            title_hits = self._keyword_hits_in_title(topic)
            keyword_hits = len(topic.matched_keywords)
            if title_hits < self.MIN_TITLE_HITS_FOR_RELAXED and keyword_hits < self.MIN_KEYWORD_HITS_FOR_RELAXED:
                continue
            published = self._parse_datetime(topic.published_at)
            if published is not None and published.astimezone(timezone.utc) < relaxed_cutoff:
                continue
            pool.append(topic)

        deduped = self._dedupe_topics(pool)
        return self._rank_topics(deduped)

    def _passes_topic_focus(
        self,
        topic: TopicCandidate,
        filtered_out: dict[str, int],
    ) -> bool:
        pattern_score = self._high_performance_pattern_score(topic)
        title_text = str(topic.title or "")
        body_text = f"{topic.title} {topic.summary} {topic.snippet}"
        # 热榜来源：标题即热点。只要账号启用了 hot_topic 赛道，且热度达标，
        # 不要求关键词命中（热点标题往往不含行业关键词，但浏览量价值最高）。
        if (
            topic.source_type in self.HOT_SOURCE_TYPES
            and "hot_topic" in set(self.topic_settings.focus_industries)
            and topic.heat_value >= self.HOT_HEAT_THRESHOLD
        ):
            # 敏感/高危话题直接拦截，避免账号被平台限流或封禁
            if any(token in title_text or token in body_text for token in self.HOT_TOPIC_BLOCKLIST):
                filtered_out["low_quality"] += 1
                return False
            return True
        if self._has_blocking_quality_issue(topic):
            filtered_out["low_quality"] += 1
            return False
        if "international" in set(self.topic_settings.focus_industries):
            if any(token in title_text for token in self.INTERNATIONAL_FALSE_POSITIVE_TOKENS):
                filtered_out["weak_focus"] += 1
                return False
            if any(token in body_text for token in self.INTERNATIONAL_CONTEXT_FALSE_POSITIVE_TOKENS):
                filtered_out["weak_focus"] += 1
                return False
            if "国际" in title_text and not any(
                token in title_text
                for token in (
                    "美国",
                    "欧洲",
                    "俄罗斯",
                    "乌克兰",
                    "中东",
                    "关税",
                    "制裁",
                    "外交",
                    "局势",
                    "停火",
                    "谈判",
                    "油价",
                    "黄金",
                    "白宫",
                    "欧盟",
                    "北约",
                    "古巴",
                    "上合组织",
                )
            ):
                filtered_out["weak_focus"] += 1
                return False
            if not self._has_international_signal(topic):
                filtered_out["weak_focus"] += 1
                return False
        if "sports" in set(self.topic_settings.focus_industries):
            if any(token in title_text for token in self.SPORTS_LOW_SIGNAL_TOKENS):
                filtered_out["weak_focus"] += 1
                return False
            if any(token in body_text for token in self.SPORTS_CONTEXT_FALSE_POSITIVE_TOKENS):
                filtered_out["weak_focus"] += 1
                return False
            if not self._has_sports_event_signal(topic):
                filtered_out["weak_focus"] += 1
                return False
        if "social_story" in set(self.topic_settings.focus_industries):
            # 注意：上面 hot_topic 那个分支会在热度达标时**直接 return True**，
            # 所以社会故事类账号的 config.yaml 不要同时启用 hot_topic，
            # 否则这条判据永远不会执行（篮球、时政、财报都会漏进来）。
            if not self._has_social_story_signal(topic):
                filtered_out["weak_focus"] += 1
                return False
        title_hits = self._keyword_hits_in_title(topic)
        keyword_hits = len(topic.matched_keywords)
        macro_penalty = self._macro_noise_penalty(topic)
        if self._is_weak_event_topic(topic) and pattern_score < 12 and topic.evidence_strength < 74:
            filtered_out["weak_focus"] += 1
            return False
        if macro_penalty >= 18 and self._contains_macro_title(topic):
            filtered_out["weak_focus"] += 1
            return False
        if macro_penalty >= 12 and title_hits < 2:
            filtered_out["weak_focus"] += 1
            return False
        if "notice_style_title" in set(topic.quality_flags) and topic.title_potential_score < 52:
            filtered_out["weak_focus"] += 1
            return False
        if title_hits >= 1:
            return True
        if keyword_hits >= 3 and macro_penalty == 0:
            return True
        filtered_out["weak_focus"] += 1
        return False

    def _focus_bonus(self, topic: TopicCandidate) -> int:
        title_hits = self._keyword_hits_in_title(topic)
        industry_bonus = self._focus_industry_bonus(topic)
        if title_hits >= 2:
            return 14 + industry_bonus
        if title_hits == 1:
            return 8 + industry_bonus
        if len(topic.matched_keywords) >= 3:
            return 4 + industry_bonus
        return industry_bonus

    def _has_international_signal(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        signal_hits = sum(1 for token in self.INTERNATIONAL_SIGNAL_TOKENS if token in text)
        if signal_hits >= 2:
            return True
        if signal_hits >= 1 and self._has_cross_border_signal(topic):
            return True
        return any(token in str(topic.title or "") for token in ("关税", "制裁", "外交", "停火", "油价", "黄金", "债券", "白宫"))

    def _has_sports_event_signal(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        title = str(topic.title or "")
        signal_hits = sum(1 for token in self.SPORTS_EVENT_TOKENS if token in text)
        if signal_hits >= 2:
            return True
        if signal_hits >= 1 and any(token in title for token in ("比赛", "决赛", "转会", "绝杀", "点球", "红牌", "晋级", "淘汰", "赛季", "联赛")):
            return True
        return False

    @classmethod
    def _has_social_story_signal(cls, topic: TopicCandidate) -> bool:
        """这条题有没有「普通人的生活处境」。

        与体育/国际同样三态：命中明确的跑题词直接否掉（体育、时政、娱乐、
        财经），否则要求至少一个「人与关系」或「处境与冲突」的信号词。

        做成 classmethod 是为了让 `TopicAutomationService` 的账号边界过滤也能
        直接复用同一份判据（此前它只按账号 key 硬编码，换赛道后把题全拦掉了）。
        判据只有一份，避免两处漂移。
        """
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        if any(token in text for token in cls.SOCIAL_STORY_LOW_SIGNAL_TOKENS):
            return False
        signal_hits = sum(1 for token in cls.SOCIAL_STORY_SIGNAL_TOKENS if token in text)
        if signal_hits >= 1:
            return True
        # 兜底：「偷/骗/讹/吵/闹」这类行为动词本身就是社会新闻的强信号
        return any(token in text for token in ("偷", "骗", "讹", "吵", "闹", "讹诈", "争执", "动手"))

    def _focus_industry_bonus(self, topic: TopicCandidate) -> int:
        bonus = 0
        for index, industry in enumerate(self.topic_settings.focus_industries[:5]):
            if industry in topic.matched_industries:
                bonus += max(6 - index * 2, 1)
        if "hot_topic" in topic.matched_industries:
            bonus += 4
        return bonus
    def _keyword_hits_in_title(self, topic: TopicCandidate) -> int:
        title = topic.title.lower()
        hits = 0
        for keyword in topic.matched_keywords:
            token = keyword.lower().strip()
            if token and token in title:
                hits += 1
        return hits

    def _macro_noise_penalty(self, topic: TopicCandidate) -> int:
        text = f"{topic.title} {topic.summary} {topic.snippet}".lower()
        penalty = 0
        for token in self.GENERIC_MACRO_TOKENS:
            if token.lower() in text:
                penalty += 6
        if len(topic.matched_keywords) <= 1:
            penalty += 4
        return min(penalty, 24)

    def _contains_macro_title(self, topic: TopicCandidate) -> bool:
        title = topic.title.lower()
        return any(token.lower() in title for token in self.GENERIC_MACRO_TOKENS)

    def _has_blocking_quality_issue(self, topic: TopicCandidate) -> bool:
        flags = set(topic.quality_flags)
        pattern_score = self._high_performance_pattern_score(topic)
        title_text = str(topic.title or "")
        # 热榜话题：标题即全部素材，天然 title_only。热度达标时放宽质量门禁，
        # 否则真实热榜话题会被误杀。质量把关交给 review agent。
        #
        # ⚠️ 2026-09-18：这里原来**只认 hot_topic**，而社会故事类账号必须在
        # `focus_industries` 里**去掉 hot_topic**（否则 `_passes_topic_focus` 的
        # hot_topic 分支会提前 return True，`social_story` 判据永远不执行）。
        # 两个条件互相打架，结果换赛道后热榜题全部走到下面被当成质量不达标杀掉：
        # 实测 54 条热榜题过滤后选题池为 0 条，连「男子高空作业绳子被2楼老人剪断」
        # 这种正对题的题也进不来（热榜题天然 title_only，必然命中 quality_score 判据）。
        # 所以放宽条件要同时认 social_story —— 它同样是以热榜为素材来源的赛道。
        hot_track_enabled = bool(
            {"hot_topic", "social_story"} & set(self.topic_settings.focus_industries)
        )
        is_hot_pass = (
            topic.source_type in self.HOT_SOURCE_TYPES
            and hot_track_enabled
            and topic.heat_value >= self.HOT_HEAT_THRESHOLD
        )
        if is_hot_pass:
            return False
        if "old_statistics" in flags:
            return True
        if any(token in title_text for token in self.INTERNATIONAL_FALSE_POSITIVE_TOKENS):
            return True
        if any(token in title_text for token in self.SPORTS_LOW_SIGNAL_TOKENS):
            return True
        if any(token in f"{topic.title} {topic.summary} {topic.snippet}" for token in self.SPORTS_CONTEXT_FALSE_POSITIVE_TOKENS):
            return True
        if "sports" not in set(self.topic_settings.focus_industries):
            if "title_only" in flags and topic.topic_type in {"policy_market", "industry_event"} and topic.evidence_strength < 78:
                return True
            if "title_only" in flags and not self._has_specific_subject(topic) and topic.quality_score < 72:
                return True
        else:
            if topic.source in {"bbc_football", "bbc_tennis"}:
                if "title_only" in flags and not self._has_sports_event_signal(topic) and topic.quality_score < 58:
                    return True
            elif "title_only" in flags and not self._has_sports_event_signal(topic) and topic.quality_score < 72:
                return True
        if topic.topic_type == "data_recap" and topic.quality_score < 62:
            return True
        if "weak_event" in flags and pattern_score < 12 and topic.quality_score < 74:
            return True
        if "notice_style_title" in flags and topic.title_potential_score < 46 and topic.quality_score < 78:
            return True
        if topic.quality_score < 52:
            return True
        if "weak_evidence" in flags and "weak_subject" in flags:
            return True
        if "title_only" in flags and "weak_evidence" in flags:
            return True
        return False

    def _has_specific_subject(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        if self.COMPANY_ORG_RE.search(text):
            return True
        return any(token in text for token in (*self.PROJECT_TOKENS, *self.EARNINGS_TOKENS, *self.FINANCE_TOKENS))

    def _is_title_only_topic(self, topic: TopicCandidate) -> bool:
        snippet = self._clean_text(topic.snippet)
        return not snippet or len(snippet) < 18 or self._is_unusable_topic_snippet(snippet)

    def _high_performance_pattern_score(self, topic: TopicCandidate) -> int:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        score = 0
        if self._has_scene_anchor(topic):
            score += 12
        if self._has_conflict_signal(topic):
            score += 10
        if self._has_benefit_shift_signal(topic):
            score += 10
        if self._has_cross_border_signal(topic):
            score += 8
        if self._has_specific_subject(topic):
            score += 6
        if any(token in text for token in self.EXECUTION_TOKENS):
            score += 6
        if len(topic.extracted_numbers) >= 2:
            score += 4
        if len(topic.matched_keywords) >= 3:
            score += 4
        if self._is_weak_event_topic(topic):
            score -= 12
        if "title_only" in set(topic.quality_flags):
            score -= 8
        return max(0, min(score, 40))

    def _has_scene_anchor(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        if self.SCENE_ANCHOR_RE.search(text):
            return True
        return any(token in text for token in self.SCENE_TOKENS)

    def _has_conflict_signal(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        if "不是" in text and "而是" in text:
            return True
        return any(token in text for token in self.CONFLICT_TOKENS)

    def _has_cross_border_signal(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        hits = sum(1 for token in self.CROSS_BORDER_TOKENS if token in text)
        return hits >= 2

    def _has_benefit_shift_signal(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        return any(token in text for token in self.BENEFIT_SHIFT_TOKENS) and any(
            token in text for token in (*self.EXECUTION_TOKENS, *self.CONFLICT_TOKENS)
        )

    def _is_weak_event_topic(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        if not any(token in text for token in self.WEAK_EVENT_TOKENS):
            return False
        if self._has_scene_anchor(topic) or self._has_conflict_signal(topic):
            return False
        if any(token in text for token in self.EXECUTION_TOKENS):
            return False
        if len(topic.extracted_numbers) >= 2:
            return False
        return True

    def _is_notice_style_title(self, topic: TopicCandidate) -> bool:
        title = str(topic.title or "").strip()
        if not title:
            return False
        if self.NOTICE_STYLE_RE.search(title):
            return True
        if any(token in title for token in self.NOTICE_STYLE_TOKENS):
            if self._title_has_subject_anchor(topic):
                if any(token in title for token in self.CONFLICT_TOKENS):
                    return False
                if any(token in title for token in ("背后", "谁", "为什么", "真正", "先受益", "先赚钱")):
                    return False
            return True
        if ("|" in title or "｜" in title) and self.PRICE_HEADLINE_RE.search(title):
            return True
        return False

    def _title_has_subject_anchor(self, topic: TopicCandidate) -> bool:
        title = str(topic.title or "")
        if self.COMPANY_ORG_RE.search(title):
            return True
        if any(token in title for token in self.SCENE_TOKENS):
            return True
        hits = 0
        for keyword in topic.matched_keywords[:4]:
            token = str(keyword or "").strip()
            if token and token in title:
                hits += 1
        return hits >= 2

    def _selection_penalty(self, topic: TopicCandidate) -> int:
        penalty = 0
        if self._is_notice_style_title(topic):
            penalty += 12
        if topic.title_potential_score < 50:
            penalty += 6
        if self._is_weak_event_topic(topic) and topic.title_potential_score < 58:
            penalty += 6
        return penalty

    def _is_old_statistics_topic(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary}"
        if not any(token in text for token in self.DATA_RECAP_TOKENS):
            return False
        year_matches = [int(item) for item in re.findall(r"(20\d{2})", text)]
        current_year = datetime.now().year
        if any(year <= current_year - 2 for year in year_matches):
            return True
        if "统计数据表" in text or "产量统计" in text:
            return True
        return False

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _normalize_title(self, title: str) -> str:
        base = title.split(" - ")[0].strip()
        return self.TITLE_NORMALIZE_PATTERN.sub("", base.lower())

    def _title_similarity(self, first: str, second: str) -> float:
        if not first or not second:
            return 0.0
        if first == second:
            return 1.0
        min_length = min(len(first), len(second))
        common = sum(1 for left, right in zip(first, second) if left == right)
        return common / max(min_length, 1)

    def _clean_text(self, text: str) -> str:
        text = html.unescape(text or "")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    async def _hydrate_selected_topic_previews(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        if not topics:
            return topics

        semaphore = asyncio.Semaphore(self.TOPIC_PREVIEW_CONCURRENCY)

        async def refresh_with_limit(topic: TopicCandidate) -> None:
            async with semaphore:
                await self._refresh_topic_preview(topic)

        tasks = [
            refresh_with_limit(topic)
            for topic in topics
            if self._should_refresh_topic_preview(topic)
        ]
        if not tasks:
            return topics

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                self.logger.warning("Unexpected selected-topic preview refresh failure: %s", result)
        return topics

    def _should_refresh_topic_preview(self, topic: TopicCandidate) -> bool:
        if not topic.url:
            return False

        source_type = str(topic.source_type or "").strip().lower()
        if source_type not in {"html", "rss", "google_rss"}:
            return False

        snippet = str(topic.snippet or "").strip()
        if source_type in {"rss", "google_rss"}:
            # RSS descriptions are frequently only a headline plus one short
            # sentence.  Fetch the selected item's canonical page so the
            # writer receives facts, while retaining already evidence-rich
            # feed entries and avoiding a fetch for every RSS candidate.
            return (
                self._is_unusable_topic_snippet(snippet)
                or len(self._clean_text(snippet)) < self.RSS_PREVIEW_MIN_LENGTH
                or not self._is_evidence_rich_topic_text(snippet)
            )

        if not snippet:
            return True
        if self._is_unusable_topic_snippet(snippet):
            return True
        if topic.source in {"newenergy_ofweek", "chuneng_ofweek"} and len(snippet) > 100:
            return not self._is_evidence_rich_topic_text(snippet)
        if topic.source in {"zhidx", "ofweek"} and len(snippet) <= 120:
            return True
        return False

    async def _refresh_topic_preview(self, topic: TopicCandidate) -> None:
        try:
            html_text = await self.fetch_text(topic.url)
        except Exception as exc:
            self.logger.debug("Failed to refresh topic preview for %s: %s", topic.url, exc)
            return

        current_snippet = self._clean_text(topic.snippet)
        context = self._extract_article_context(html_text, topic.title)
        if context and len(context) > len(current_snippet) + 80:
            topic.snippet = context
            if not topic.published_at:
                date_match = re.search(
                    r"(?:20\d{2}年(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日|"
                    r"(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日)",
                    context,
                )
                if date_match:
                    topic.published_at = date_match.group(0)
            self._enrich_topic(topic)
            return

        preview = self._extract_article_preview(html_text, topic.title)
        if not preview:
            return

        topic.snippet = preview[:260]
        self._enrich_topic(topic)

    def _extract_article_context(self, html_text: str, title: str) -> str:
        fragments: list[str] = []
        seen: set[str] = set()
        for raw in self.ARTICLE_PARAGRAPH_PATTERN.findall(html_text):
            paragraph = self._clean_text(raw)
            if not paragraph or len(paragraph) < 24:
                continue
            if title and paragraph == title:
                continue
            if re.match(r"^(?:作者|编辑|来源|责任编辑|发布时间)\s*[|｜:：]", paragraph):
                continue
            if any(marker in paragraph for marker in ("原文标题", "原标题", "相关阅读", "推荐阅读", "免责声明")):
                break
            if "#" in paragraph or "关注「" in paragraph or "关注“" in paragraph:
                break

            normalized = re.sub(r"[\s\W_]+", "", paragraph).lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            fragments.append(paragraph)
            context = "\n\n".join(fragments)
            if len(context) >= self.ARTICLE_CONTEXT_MAX_LENGTH:
                return context[: self.ARTICLE_CONTEXT_MAX_LENGTH]

        context = "\n\n".join(fragments).strip()
        if len(context) < self.ARTICLE_CONTEXT_MIN_LENGTH:
            return ""
        return context[: self.ARTICLE_CONTEXT_MAX_LENGTH]

    def _extract_article_preview(self, html_text: str, title: str) -> str:
        for pattern in self.META_DESCRIPTION_PATTERNS:
            match = pattern.search(html_text)
            if not match:
                continue
            preview = self._normalize_article_preview(match.group(1), title)
            if preview:
                return preview

        for raw in self.ARTICLE_PARAGRAPH_PATTERN.findall(html_text):
            preview = self._normalize_article_preview(raw, title)
            if preview:
                return preview
        return ""

    def _normalize_article_preview(self, text: str, title: str) -> str:
        preview = self._clean_text(text)
        if not preview:
            return ""
        if title and preview.startswith(title):
            preview = preview[len(title) :].strip(" ：:-")
        preview = re.sub(
            r"^(?:刚刚|最新(?:动态)?|\d+\s*(?:分钟前|小时前|天前)|(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2})(?:\s+\d{1,2}:\d{2})?)[:：\- ]*",
            "",
            preview,
        ).strip()
        preview = preview.lstrip("，,；;| ")
        preview = re.sub(
            r"[，,。；;…\s]*(?:国际储能网|国际新能源网|新浪财经|车家号|新浪新闻_手机新浪网)\s*$",
            "",
            preview,
        ).strip()
        if len(preview) < 24 or len(preview) > 220:
            return ""
        if self._is_unusable_topic_snippet(preview):
            return ""
        return preview

    def _unique(self, items: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = item.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result


class _BaseTopicCollector:
    def __init__(
        self,
        source_name: str,
        source_config: dict[str, object],
        service: TopicCollectionService,
    ) -> None:
        self.source_name = source_name
        self.source_config = source_config
        self.service = service

    async def collect(self) -> TopicSourceResult:
        raise NotImplementedError


class _HotBoardTopicCollector(_BaseTopicCollector):
    """头条热榜实时热点采集（官方 JSON API，含热度值）。

    URL: https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc
    返回 data[] 数组，每项含 Title / HotValue / Url / Label。
    """

    TOUTIAO_HOT_BOARD_URL = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])] or [self.TOUTIAO_HOT_BOARD_URL]
        results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_hot_board(payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_hot_board(self, payload: str) -> list[TopicCandidate]:
        data = json.loads(payload)
        board_items = data.get("data") or []
        candidates: list[TopicCandidate] = []
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for item in board_items:
            if not isinstance(item, dict):
                continue
            title = self.service._clean_text(str(item.get("Title", "") or ""))
            if not title:
                continue
            heat_raw = str(item.get("HotValue", "") or "").strip()
            heat_value = 0
            if heat_raw.isdigit():
                heat_value = int(heat_raw)
            url = str(item.get("Url", "") or "")
            if not url.startswith("http"):
                url = f"https://www.toutiao.com/trending/{item.get('ClusterId', '')}"
            candidates.append(
                TopicCandidate(
                    title=title,
                    url=url,
                    source=self.source_name,
                    source_type="hot_board",
                    published_at=now_iso,
                    snippet="",
                    heat_value=heat_value,
                )
            )
        return candidates


class _BaiduHotTopicCollector(_BaseTopicCollector):
    """百度实时热搜采集（HTML 内嵌 JSON）。

    URL: https://top.baidu.com/board?tab=realtime
    页面内嵌 "query":"标题" 与 "hotScore":"热度" 的 JSON 片段。
    """

    BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"
    QUERY_PATTERN = re.compile(r'"query":"((?:[^"\\]|\\.)*)"')
    HOTSCORE_PATTERN = re.compile(r'"hotScore":"?(\d+)"?')

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])] or [self.BAIDU_HOT_URL]
        results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_hot_page(payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_hot_page(self, payload: str) -> list[TopicCandidate]:
        queries = self.QUERY_PATTERN.findall(payload)
        scores = [int(value) for value in self.HOTSCORE_PATTERN.findall(payload)]
        candidates: list[TopicCandidate] = []
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for index, raw_title in enumerate(queries[: self.service.topic_settings.max_items_per_source]):
            title = self.service._clean_text(raw_title)
            if not title or len(title) < 6:
                continue
            heat_value = scores[index] if index < len(scores) else 0
            candidates.append(
                TopicCandidate(
                    title=title,
                    url="",
                    source=self.source_name,
                    source_type="baidu_hot",
                    published_at=now_iso,
                    snippet="",
                    heat_value=heat_value,
                )
            )
        return candidates


class _RssTopicCollector(_BaseTopicCollector):
    def __init__(
        self,
        source_name: str,
        source_config: dict[str, object],
        service: TopicCollectionService,
        google_mode: bool,
    ) -> None:
        super().__init__(source_name, source_config, service)
        self.google_mode = google_mode

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])]
        tasks = [self.service.fetch_text(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_rss(payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_rss(self, xml_text: str) -> list[TopicCandidate]:
        root = ElementTree.fromstring(xml_text)
        candidates: list[TopicCandidate] = []
        for item in root.findall(".//item"):
            title = self.service._clean_text(item.findtext("title", default=""))
            link = self.service._clean_text(item.findtext("link", default=""))
            description = item.findtext("description", default="")
            pub_date = self.service._clean_text(item.findtext("pubDate", default=""))

            if self.google_mode and " - " in title:
                title = title.rsplit(" - ", 1)[0].strip()

            snippet = self.service._clean_text(description)
            if not title or not link:
                continue
            candidates.append(
                TopicCandidate(
                    title=title,
                    url=link,
                    source=self.source_name,
                    source_type="rss",
                    published_at=pub_date,
                    snippet=snippet[:260],
                )
            )
        return candidates


class _ZhidxTopicCollector(_BaseTopicCollector):
    ARTICLE_PATTERN = re.compile(
        r'<a[^>]+href="(https://www\.zhidx\.com/p/\d+\.html)"[^>]+title="([^"]+)"[^>]*>(.*?)</a>',
        re.S,
    )
    DESC_PATTERN = re.compile(r'<div class="(?:info-left-desc|item-text|article-desc)">(.+?)</div>', re.S)
    DATE_PATTERN = re.compile(r'(\d{2}-\d{2}|\d{4}-\d{2}-\d{2})')

    async def collect(self) -> TopicSourceResult:
        try:
            items: list[TopicCandidate] = []
            errors: list[str] = []
            urls = [str(item) for item in self.source_config.get("urls", [])]
            results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

            for url, payload in zip(urls, results):
                if isinstance(payload, Exception):
                    errors.append(f"{url}: {payload}")
                    continue
                try:
                    items.extend(self._parse_page(url, payload))
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(f"{url}: {exc}")

            return TopicSourceResult(
                source_name=self.source_name,
                items=items[: self.service.topic_settings.max_items_per_source],
                errors=errors,
            )
        except Exception as exc:
            self.service.logger.warning(
                "ZhidxTopicCollector %s failed entirely: %s", self.source_name, exc,
            )
            return TopicSourceResult(
                source_name=self.source_name,
                items=[],
                errors=[f"Collector {self.source_name} failed entirely: {exc}"],
            )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        candidates: list[TopicCandidate] = []
        for match in self.ARTICLE_PATTERN.finditer(html_text):
            url = match.group(1)
            title = self.service._clean_text(match.group(2))
            if not title or "#comments" in url:
                continue

            window = html_text[match.end() : match.end() + 600]
            desc_match = self.DESC_PATTERN.search(window)
            date_match = self.DATE_PATTERN.search(window)
            snippet = self.service._clean_text(desc_match.group(1)) if desc_match else ""
            published_at = date_match.group(1) if date_match else ""

            candidates.append(
                TopicCandidate(
                    title=title,
                    url=urljoin(page_url, url),
                    source=self.source_name,
                    source_type="html",
                    published_at=published_at,
                    snippet=snippet[:260],
                )
            )
        return candidates


class _OfweekTopicCollector(_BaseTopicCollector):
    LINK_PATTERN = re.compile(r'href="([^"]+ART[^"]+\.html)"[^>]*>(.*?)</a>', re.S)
    PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
    DATE_PATTERN = re.compile(r"(20\d{2}-\d{2}-\d{2}|\d{2}-\d{2})")

    async def collect(self) -> TopicSourceResult:
        try:
            items: list[TopicCandidate] = []
            errors: list[str] = []
            urls = [str(item) for item in self.source_config.get("urls", [])]
            results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

            for url, payload in zip(urls, results):
                if isinstance(payload, Exception):
                    errors.append(f"{url}: {payload}")
                    continue
                try:
                    items.extend(self._parse_page(url, payload))
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(f"{url}: {exc}")

            return TopicSourceResult(
                source_name=self.source_name,
                items=items[: self.service.topic_settings.max_items_per_source],
                errors=errors,
            )
        except Exception as exc:
            self.service.logger.warning(
                "OfweekTopicCollector %s failed entirely: %s", self.source_name, exc,
            )
            return TopicSourceResult(
                source_name=self.source_name,
                items=[],
                errors=[f"Collector {self.source_name} failed entirely: {exc}"],
            )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        candidates: list[TopicCandidate] = []
        seen_titles: set[str] = set()
        for match in self.LINK_PATTERN.finditer(html_text):
            url = self._absolutize(match.group(1))
            title = self.service._clean_text(match.group(2))
            if not title or len(title) < 4:
                continue
            if title in seen_titles:
                continue
            seen_titles.add(title)

            window = html_text[match.end() : match.end() + 700]
            paragraphs = [self.service._clean_text(item) for item in self.PARAGRAPH_PATTERN.findall(window)]
            snippet = next((item for item in paragraphs if item and item != title), "")
            published_match = self.DATE_PATTERN.search(window)
            published_at = published_match.group(1) if published_match else ""
            candidates.append(
                TopicCandidate(
                    title=title,
                    url=urljoin(page_url, url),
                    source=self.source_name,
                    source_type="html",
                    published_at=published_at,
                    snippet=snippet[:260],
                )
            )
        return candidates

    def _absolutize(self, url: str) -> str:
        return f"https:{url}" if url.startswith("//") else url


class _GenericHtmlTopicCollector(_BaseTopicCollector):
    LINK_PATTERN = re.compile(r'<a[^>]+href="([^"]+)"[^>]*(?:title="([^"]*)")?[^>]*>(.*?)</a>', re.S)
    RELATIVE_DATE_PATTERN = re.compile(r"(刚刚|最新|最新动态|\d+\s*(?:分钟|小时|天)前)")
    FULL_DATE_PATTERN = re.compile(
        r"((?:20\d{2}[./-](?:0?[1-9]|1[0-2])[./-](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
        r"|(?:20\d{2}年(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?))"
    )
    CHINESE_MONTH_DAY_PATTERN = re.compile(
        r"((?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日(?:\s+\d{1,2}:\d{2})?)"
    )
    SHORT_DATE_PATTERN = re.compile(
        r"((?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2})?)"
    )
    BLOCK_PATTERN = re.compile(r"<(?:p|span|div|li)[^>]*>(.*?)</(?:p|span|div|li)>", re.S)
    TITLE_EXCLUSIONS = ("首页", "更多", "登录", "注册", "专题", "展会", "会议", "报告", "视频", "直播", "下载", "门户网站")

    def __init__(
        self,
        source_name: str,
        source_config: dict[str, object],
        service: TopicCollectionService,
        same_domain_only: bool,
    ) -> None:
        super().__init__(source_name, source_config, service)
        self.same_domain_only = same_domain_only

    async def collect(self) -> TopicSourceResult:
        try:
            items: list[TopicCandidate] = []
            errors: list[str] = []
            urls = [str(item) for item in self.source_config.get("urls", [])]
            results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

            for url, payload in zip(urls, results):
                if isinstance(payload, Exception):
                    errors.append(f"{url}: {payload}")
                    continue
                try:
                    items.extend(self._parse_page(url, payload))
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(f"{url}: {exc}")

            return TopicSourceResult(
                source_name=self.source_name,
                items=self._rank_items(items)[: self.service.topic_settings.max_items_per_source],
                errors=errors,
            )
        except Exception as exc:
            self.service.logger.warning(
                "GenericHtmlTopicCollector %s failed entirely: %s", self.source_name, exc,
            )
            return TopicSourceResult(
                source_name=self.source_name,
                items=[],
                errors=[f"Collector {self.source_name} failed entirely: {exc}"],
            )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        base_domain = urlparse(page_url).netloc
        candidates: list[TopicCandidate] = []
        seen_titles: set[str] = set()
        for match in self.LINK_PATTERN.finditer(html_text):
            href, title_attr, inner_html = match.groups()
            url = self._absolutize(page_url, href)
            if not url.startswith("http"):
                continue
            if self.same_domain_only and urlparse(url).netloc != base_domain:
                continue

            title = self.service._clean_text(title_attr or inner_html)
            if len(title) < 8 or len(title) > 40 or "javascript:" in href.lower():
                continue
            if not re.search(r"[\u4e00-\u9fff]", title):
                continue
            if title in seen_titles:
                continue
            if any(token in title for token in self.TITLE_EXCLUSIONS):
                continue

            before_window = html_text[max(0, match.start() - 260) : match.start()]
            after_window = html_text[match.end() : match.end() + 1400]
            context = f"{before_window} {after_window}"
            published_at = self._extract_published_at(context)
            snippet = self._extract_snippet(after_window, context, title)
            seen_titles.add(title)

            candidates.append(
                TopicCandidate(
                    title=title,
                    url=url,
                    source=self.source_name,
                    source_type="html",
                    published_at=published_at,
                    snippet=snippet[:260],
                )
            )
        return candidates

    def _absolutize(self, page_url: str, href: str) -> str:
        if href.startswith("//"):
            return f"https:{href}"
        return urljoin(page_url, href)

    def _extract_snippet(self, html_window: str, context_window: str, title: str) -> str:
        fragments: list[str] = []
        seen: set[str] = set()

        for window in (html_window, context_window):
            for raw in self.BLOCK_PATTERN.findall(window):
                text = self.service._clean_text(raw.split("<a", 1)[0])
                if not text or text == title:
                    continue
                if len(text) < 12 or len(text) > 220:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", text):
                    continue
                text = self._normalize_snippet(text, title)
                if not text or text in seen:
                    continue
                seen.add(text)
                fragments.append(text)

        if fragments:
            return max(fragments, key=lambda item: self._score_snippet(item, title))

        plain_text = self.service._clean_text(re.sub(r"<[^>]+>", " ", html_window))
        plain_text = self._normalize_snippet(plain_text, title)
        return plain_text[:180]

    def _extract_published_at(self, context: str) -> str:
        for pattern in (
            self.RELATIVE_DATE_PATTERN,
            self.FULL_DATE_PATTERN,
            self.CHINESE_MONTH_DAY_PATTERN,
            self.SHORT_DATE_PATTERN,
        ):
            match = pattern.search(context)
            if match:
                return match.group(1)
        return ""

    def _rank_items(self, items: list[TopicCandidate]) -> list[TopicCandidate]:
        return sorted(items, key=self._score_item, reverse=True)

    def _score_item(self, item: TopicCandidate) -> tuple[int, int, int]:
        score = 0
        combined = f"{item.title} {item.snippet}"
        if item.published_at:
            score += 8
        if item.snippet and len(item.snippet) >= 20:
            score += 6
        if item.snippet and not self.service._is_low_signal_topic_text(item.snippet):
            score += 8
        if re.search(r"\d", combined):
            score += 3
        if any(token in combined for token in ("储能", "新能源", "电池", "项目", "并网", "中标", "订单", "装机", "电站")):
            score += 8
        if any(token in item.title for token in self.TITLE_EXCLUSIONS):
            score -= 12
        if len(item.title) < 10:
            score -= 5
        return score, len(item.snippet), len(item.title)

    def _score_snippet(self, text: str, title: str) -> int:
        if not text or self.service._is_low_signal_topic_text(text):
            return -100
        score = min(len(text), 120)
        if re.search(r"\d", text):
            score += 12
        if any(token in text for token in ("项目", "订单", "交付", "并网", "装机", "电站", "招标", "中标", "投产", "签约")):
            score += 14
        if any(token in text for token in ("储能", "电池", "光伏", "风电", "逆变器", "抽水蓄能", "超级电容", "压缩空气储能")):
            score += 16
        if "：" in text or "，" in text:
            score += 6
        if text.startswith(title):
            score -= 8
        return score

    def _normalize_snippet(self, text: str, title: str = "") -> str:
        snippet = str(text or "").strip()
        if not snippet:
            return ""
        snippet = re.sub(r"^(最新动态|更多)\s*", "", snippet).strip()
        snippet = snippet.split("了解更多", 1)[0].strip()
        snippet = re.sub(
            r"^(?:刚刚|最新(?:动态)?|\d+\s*(?:分钟前|小时前|天前)|(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2})(?:\s+\d{1,2}:\d{2})?)[:：\- ]*",
            "",
            snippet,
        ).strip()
        if title and snippet.startswith(title):
            snippet = snippet[len(title) :].strip(" ：:-")
        if len(re.findall(r"\d+\s*(?:分钟前|小时前|天前)", snippet)) >= 2:
            return ""
        if self.service._is_low_signal_topic_text(snippet):
            return ""
        return re.sub(r"\s+", " ", snippet).strip()
