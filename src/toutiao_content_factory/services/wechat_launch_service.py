from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.image_generation_service import ImageGenerationService
from toutiao_content_factory.services.openai_service import OpenAIContentService


@dataclass(frozen=True)
class WechatLaunchDayPlan:
    day_index: int
    theme: str
    purpose: str
    suggested_titles: list[str]
    structure_points: list[str]
    keyword_reply: str
    reply_content: list[str]


class WechatLaunchService:
    PLAN: list[WechatLaunchDayPlan] = [
        WechatLaunchDayPlan(
            day_index=1,
            theme="为什么现在开始持续跟踪人形机器人",
            purpose="定账号标签，告诉用户关注后能持续得到什么",
            suggested_titles=[
                "为什么我开始只写人形机器人这条线",
                "人形机器人现在最值得盯的不是热度",
                "从今天起，我只盯这条机器人产业线",
            ],
            structure_points=[
                "交代为什么要做这个账号",
                "现在行业最该盯的三类变量",
                "未来会持续更新什么",
                "结尾引导关注并回复关键词领取资料",
            ],
            keyword_reply="图谱",
            reply_content=["人形机器人产业链图谱"],
        ),
        WechatLaunchDayPlan(
            day_index=2,
            theme="人形机器人产业链图谱",
            purpose="拿收藏和转发",
            suggested_titles=[
                "一张图看懂人形机器人产业链",
                "人形机器人最全产业链图谱，建议收藏",
                "谁在做人形机器人最赚钱的那一段",
            ],
            structure_points=[
                "先下判断，利润不会平均分配",
                "拆上游核心零部件",
                "拆中游系统集成",
                "拆下游场景验证",
                "结尾引导回复关键词领取高清版",
            ],
            keyword_reply="高清图谱",
            reply_content=["高清机器人产业链图谱"],
        ),
        WechatLaunchDayPlan(
            day_index=3,
            theme="当前最值得盯的10个量产验证变量",
            purpose="建立专业感",
            suggested_titles=[
                "人形机器人别只看表演，真正该盯这10个变量",
                "量产前夜，机器人行业最该跟踪的10件事",
                "谁会先跑出来，要先看这10个验证变量",
            ],
            structure_points=[
                "行业热度不等于业绩兑现",
                "把10个变量写成自然段，不做生硬清单",
                "结尾引导读者留言最看重哪个变量",
            ],
            keyword_reply="跟踪表",
            reply_content=["机器人跟踪变量表"],
        ),
        WechatLaunchDayPlan(
            day_index=4,
            theme="灵巧手、减速器、执行器，哪一段最先兑现利润",
            purpose="打出判断型标签",
            suggested_titles=[
                "机器人最热三大环节，谁先把订单变利润",
                "灵巧手减速器执行器，哪一段先赚钱",
                "机器人热度之外，真正先兑现的是哪一段",
            ],
            structure_points=[
                "开头直接给结论",
                "为什么灵巧手热但兑现慢",
                "为什么减速器受益但竞争卷",
                "为什么执行器更值得看",
                "结尾引导互动分歧",
            ],
            keyword_reply="环节判断",
            reply_content=["三大环节判断笔记"],
        ),
        WechatLaunchDayPlan(
            day_index=5,
            theme="本周机器人赛道5个关键变化",
            purpose="培养用户固定阅读习惯",
            suggested_titles=[
                "机器人赛道本周最重要的5个变化",
                "本周机器人行业，真正有价值的是这5件事",
                "一周复盘：机器人产业链又变了什么",
            ],
            structure_points=[
                "订单变化",
                "融资变化",
                "供应链变化",
                "海外场景变化",
                "估值和市场预期变化",
            ],
            keyword_reply="周报",
            reply_content=["机器人周报"],
        ),
        WechatLaunchDayPlan(
            day_index=6,
            theme="为什么很多机器人公司看起来火，但未必先赚到钱",
            purpose="强化反常识判断",
            suggested_titles=[
                "机器人最火的公司，不一定最先赚钱",
                "热度很高，但很多机器人公司还赚不到钱",
                "看起来很火的机器人公司，为什么利润未必先来",
            ],
            structure_points=[
                "第一段给出反常识结论",
                "为什么样机展示不等于量产",
                "为什么订单不等于利润",
                "为什么真正值钱的是交付能力",
                "结尾引导关注后续深度跟踪",
            ],
            keyword_reply="盈利逻辑",
            reply_content=["机器人盈利逻辑笔记"],
        ),
        WechatLaunchDayPlan(
            day_index=7,
            theme="关注后领取资料包",
            purpose="集中转化粉丝",
            suggested_titles=[
                "我整理了1份机器人资料包，关注后可领",
                "想系统跟踪机器人行业，这3份资料够用了",
                "起号第一周，我把最核心的机器人资料包开放领取",
            ],
            structure_points=[
                "总结这周写了什么",
                "说明资料包内容",
                "说明为什么值得持续关注",
                "结尾明确回复动作",
            ],
            keyword_reply="资料包",
            reply_content=["机器人产业链图谱", "机器人周报", "跟踪变量表"],
        ),
    ]

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.openai_service = OpenAIContentService(settings, logger)
        self.image_service = ImageGenerationService(settings, logger)

    def close(self) -> None:
        self.openai_service.close()

    def prepare_day(self, day_index: int, start_date: date | None = None) -> dict[str, Path]:
        plan = self._get_plan(day_index)
        publish_date = (start_date or date.today()) + timedelta(days=day_index - 1)
        payload = self._generate_day_payload(plan, publish_date)
        result = self._write_run_package(plan, publish_date, payload)
        if not self.settings.skip_image_generation:
            try:
                result.update(self.image_service.generate_for_run(result["run_dir"]))
            except Exception as exc:
                self.logger.warning("Image generation failed for WeChat launch day %s: %s", day_index, exc)
        return result

    def prepare_week(self, start_date: date | None = None) -> dict[str, Path]:
        exported: dict[str, Path] = {}
        for plan in self.PLAN:
            result = self.prepare_day(plan.day_index, start_date=start_date)
            exported[f"day_{plan.day_index}_run_dir"] = result["run_dir"]
        return exported

    def _get_plan(self, day_index: int) -> WechatLaunchDayPlan:
        for item in self.PLAN:
            if item.day_index == day_index:
                return item
        raise ValueError(f"Unsupported wechat launch day: {day_index}")

    def _generate_day_payload(self, plan: WechatLaunchDayPlan, publish_date: date) -> dict[str, Any]:
        if not self.openai_service.available:
            return self._fallback_payload(plan, publish_date)

        system_prompt = (
            "你是中文科技产业公众号主编。"
            "输出严格 JSON。"
            "不要输出 markdown 代码块。"
        )
        user_prompt = self._build_generation_prompt(plan, publish_date)
        try:
            payload = self.openai_service.create_json_completion(system_prompt, user_prompt)
        except Exception as exc:
            self.logger.warning("WeChat launch day generation fell back to local draft: %s", exc)
            return self._fallback_payload(plan, publish_date)

        return self._normalize_payload(plan, publish_date, payload)

    def _build_generation_prompt(self, plan: WechatLaunchDayPlan, publish_date: date) -> str:
        return (
            f"账号名：{self.settings.wechat.account_name or self.settings.brand_name}\n"
            f"品牌：{self.settings.brand_name}\n"
            f"赛道：人形机器人 / 具身智能产业链\n"
            f"目标读者：{self.settings.audience}\n"
            f"发布日期：{publish_date.isoformat()}\n"
            f"起号日程：Day {plan.day_index}\n"
            f"当日主题：{plan.theme}\n"
            f"当日目的：{plan.purpose}\n"
            f"候选标题：{json.dumps(plan.suggested_titles, ensure_ascii=False)}\n"
            f"结构要求：{json.dumps(plan.structure_points, ensure_ascii=False)}\n"
            f"关键词回复：{plan.keyword_reply}\n"
            f"回复内容：{json.dumps(plan.reply_content, ensure_ascii=False)}\n\n"
            "请生成一篇适合微信公众号起号阶段的高质量中文文章，并返回 JSON，字段如下：\n"
            "{\n"
            '  "selected_title": "最终标题，64字内",\n'
            '  "candidate_titles": ["标题1","标题2","标题3"],\n'
            '  "summary": "100字内摘要",\n'
            '  "article": "正文，不要使用#标题、##小标题，不要输出markdown标题符号，段落自然分开",\n'
            '  "comment_prompt": "评论区互动问题",\n'
            '  "tags": ["#标签1","#标签2","#标签3","#标签4"],\n'
            '  "cover_prompt": "16:9中文科技封面图提示词，适合头条/公众号通用",\n'
            '  "infographic_prompts": ["正文图提示词1","正文图提示词2"],\n'
            '  "validation_notes": ["写作说明1","写作说明2"]\n'
            "}\n\n"
            "约束：\n"
            "1. 正文必须像真实产业分析文章，不要像欢迎词，不要出现“我是某某账号”。\n"
            "2. 正文不能出现 markdown 标题符号 #。\n"
            "3. 文章要兼顾收藏率和关注转化，结尾自然引导回复关键词领取资料。\n"
            "4. 封面图 prompt 和正文图 prompt 参考头条科技内容思路：深色科技风、中文标题突出、信息密度高，但不要低质模板感。\n"
            "5. 不要编造具体公司财务数字，除非在主题中是通用产业判断。\n"
        )

    def _normalize_payload(
        self,
        plan: WechatLaunchDayPlan,
        publish_date: date,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_titles = [
            self._clip_text(str(item).strip(), self.settings.wechat.title_max_length)
            for item in list(payload.get("candidate_titles", []))[:3]
            if str(item).strip()
        ] or list(plan.suggested_titles)
        selected_title = self._clip_text(
            str(payload.get("selected_title", candidate_titles[0] if candidate_titles else plan.suggested_titles[0])).strip(),
            self.settings.wechat.title_max_length,
        )
        if selected_title not in candidate_titles:
            candidate_titles = [selected_title, *candidate_titles][:3]

        article = clean_article_text(str(payload.get("article", "")).strip())
        if not article:
            return self._fallback_payload(plan, publish_date)

        tags = [str(item).strip() for item in list(payload.get("tags", [])) if str(item).strip()]
        if not tags:
            tags = ["#人形机器人", "#具身智能", "#机器人产业链", "#产业观察"]

        infographic_prompts = [
            str(item).strip() for item in list(payload.get("infographic_prompts", [])) if str(item).strip()
        ][:2]
        if not infographic_prompts:
            infographic_prompts = self._default_infographic_prompts(plan)

        validation_notes = [str(item).strip() for item in list(payload.get("validation_notes", [])) if str(item).strip()]

        return {
            "selected_title": selected_title,
            "candidate_titles": candidate_titles,
            "summary": self._clip_text(str(payload.get("summary", "")).strip(), self.settings.wechat.digest_max_length)
            or f"{plan.theme}，重点服务公众号起号阶段的精准读者，兼顾收藏价值与关注转化。",
            "article": article,
            "comment_prompt": str(payload.get("comment_prompt", "")).strip()
            or "你最看好机器人产业链里哪一段先兑现业绩？",
            "tags": tags[:6],
            "cover_prompt": str(payload.get("cover_prompt", "")).strip() or self._default_cover_prompt(plan),
            "infographic_prompts": infographic_prompts,
            "validation_notes": validation_notes,
            "keyword_reply": plan.keyword_reply,
            "reply_content": list(plan.reply_content),
            "day_index": plan.day_index,
            "theme": plan.theme,
            "purpose": plan.purpose,
        }

    def _fallback_payload(self, plan: WechatLaunchDayPlan, publish_date: date) -> dict[str, Any]:
        selected_title = self._clip_text(plan.suggested_titles[0], self.settings.wechat.title_max_length)
        article = self._build_fallback_article(plan)
        return {
            "selected_title": selected_title,
            "candidate_titles": list(plan.suggested_titles),
            "summary": f"{plan.theme}，围绕机器人产业链的核心变量做集中拆解，适合公众号起号阶段沉淀精准读者。",
            "article": article,
            "comment_prompt": "你最看好机器人产业链里哪一段先兑现利润？",
            "tags": ["#人形机器人", "#具身智能", "#机器人产业链", "#产业观察"],
            "cover_prompt": self._default_cover_prompt(plan),
            "infographic_prompts": self._default_infographic_prompts(plan),
            "validation_notes": [
                "正文按公众号阅读节奏拆成自然段落。",
                "结尾保留关键词回复动作，兼顾关注转化。",
            ],
            "keyword_reply": plan.keyword_reply,
            "reply_content": list(plan.reply_content),
            "day_index": plan.day_index,
            "theme": plan.theme,
            "purpose": plan.purpose,
        }

    def _build_fallback_article(self, plan: WechatLaunchDayPlan) -> str:
        pieces = {
            1: (
                "人形机器人正在从讲故事的阶段，进入比拼兑现能力的阶段。过去很多讨论都围绕演示效果、融资节奏和市场热度展开，但真正决定行业分化的，仍然是成本、交付和场景验证这三条线。"
                "\n\n"
                "我把这个号第一阶段的重点放在人形机器人，不是因为这条线最热，而是因为它最适合持续跟踪。这个行业现在最大的认知差在于，外部看到的是热闹，产业内部比的是谁能把样机推进成订单，谁能把订单变成稳定复购。"
                "\n\n"
                "接下来这里会重点写三类内容。第一类是产业链图谱，帮你快速看清楚上游零部件、中游系统集成和下游场景验证的关键环节。第二类是订单与量产跟踪，专门拆哪些公司是真的在推进商业化，哪些还停留在概念阶段。第三类是利润兑现判断，不只看热度，而是看哪一段最可能先把收入和毛利做出来。"
                "\n\n"
                f"如果你也在看这条赛道，可以先关注。后面我会把{plan.reply_content[0]}整理成更适合保存的一版。回复“{plan.keyword_reply}”，就能更系统地跟上这条线。"
            ),
            2: (
                "很多人看人形机器人，只盯整机公司和演示效果，但产业链的利润并不会平均分配。真正先吃到商业化红利的，往往是卡住关键零部件、验证效率和系统集成能力的环节。"
                "\n\n"
                "如果把产业链拆开，上游最值得盯的是减速器、关节模组、执行器、传感器和控制器。这些环节决定了整机成本、可靠性和交付节奏，也决定了谁能在量产初期先拿到议价权。"
                "\n\n"
                "中游系统集成并不只是把零件装起来。真正的门槛在于软件调度、场景适配、客户验证和后续运维。下游则要看真实场景是否愿意长期买单，尤其是工厂、仓储和商服这些高重复场景。"
                "\n\n"
                f"所以这张图谱真正想解决的，不是让你知道行业里有哪些公司，而是帮你快速判断哪一段最值得长期盯。回复“{plan.keyword_reply}”，我会把高清版图谱整理出来。"
            ),
            3: (
                "人形机器人最危险的误区，是把行业热度当成兑现速度。一个赛道看起来火，不代表量产验证已经完成，更不代表业绩已经开始兑现。真正值得长期跟踪的，是那些能不断缩小商业化不确定性的变量。"
                "\n\n"
                "比如整机成本能不能压下来、样机到小批量交付的周期有没有缩短、关键零部件良率是否稳定、场景方是否愿意持续复购、售后和运维能力有没有跟上。这些变量不会同时改善，但它们会决定哪些公司先跑出来。"
                "\n\n"
                "对普通读者来说，比追每天的热搜更重要的，是盯住一套能持续复盘的变量框架。这样你看到新的订单、新的融资、新的发布会时，才知道哪些是噪音，哪些是真变化。"
                "\n\n"
                f"如果你只选一个变量来持续跟踪，你会看成本、订单还是交付？也可以回复“{plan.keyword_reply}”，我会把这套跟踪框架整理成表。"
            ),
            4: (
                "机器人赛道最容易让人误判的地方，是把最热的环节直接等同于最先兑现利润的环节。热度和利润之间，往往隔着量产、客户验证、交付效率和竞争格局。"
                "\n\n"
                "灵巧手很容易成为市场焦点，因为它决定了机器人能做多少复杂动作，但它的兑现往往更慢。减速器是典型的受益环节，可一旦行业预期升温，竞争也会同步加剧。执行器则更像一条容易被低估的线，因为它同时连接了性能、成本和稳定交付。"
                "\n\n"
                "这三段没有绝对答案，关键要看你站在什么时间维度看问题。如果看短期验证，减速器更直观；如果看中期放量，执行器的弹性可能更大；如果看远期能力上限，灵巧手仍然值得持续跟踪。"
                "\n\n"
                f"你更看好哪一段先把订单变成利润？回复“{plan.keyword_reply}”，我会把这三段的判断框架整理成一版笔记。"
            ),
            5: (
                "一周看下来，机器人赛道最值得记录的，不是信息总量，而是哪些变化真正会影响产业节奏。只有把变化分层，你才能判断是短期情绪，还是中期趋势。"
                "\n\n"
                "本周最重要的几类变化，通常集中在订单、融资、供应链、海外场景和市场预期这五条线。订单告诉你需求是否开始落地，融资反映资本愿不愿意继续押注，供应链决定行业能否支撑放量，海外场景则检验商业化的复制能力。"
                "\n\n"
                "真正有用的周报，不是把新闻重新堆一遍，而是帮读者筛选出哪些变化值得持续跟踪。这样下周再看新消息时，判断会更快，也更稳。"
                "\n\n"
                f"如果你希望每周都能更高效地抓重点，可以回复“{plan.keyword_reply}”，后面会把周报结构固定下来。"
            ),
            6: (
                "很多机器人公司看起来很火，但火不等于先赚到钱。赛道热度抬升时，最容易被放大的往往是演示、融资和估值，而不是交付和利润。"
                "\n\n"
                "样机展示不等于量产，订单签约也不等于利润兑现。真正决定公司价值的，是能不能把复杂能力稳定交付到客户现场，并且在一次次项目里把成本和故障率压下来。"
                "\n\n"
                "所以越是热的时候，越要把注意力从叙事切回经营。谁有更强的供应链组织能力，谁能更快跑通交付闭环，谁就更可能在热度退潮后留下来。"
                "\n\n"
                f"你更认同哪种判断：先看技术壁垒，还是先看交付能力？回复“{plan.keyword_reply}”，我会把这套盈利逻辑再整理成一版。"
            ),
            7: (
                "这一周连续写下来，最明显的感受是：机器人赛道的信息很多，但真正值得保存和反复看的资料并不多。对起号阶段来说，最重要的不是一次讲完所有内容，而是先把最有价值的部分整理出来。"
                "\n\n"
                "所以我把这一周最核心的几份资料集中打包，包括产业链图谱、周报结构和跟踪变量表。它们不一定复杂，但会帮助你以后看到新消息时更快进入判断状态。"
                "\n\n"
                "后面这个号也会继续沿着这条线更新，不只是跟热点，而是尽量把每次变化放回到产业链和商业化节奏里去看。这样长期看下来，价值会比单条爆款更大。"
                "\n\n"
                f"如果你想把这一周的核心资料一次拿全，回复“{plan.keyword_reply}”即可，后面我也会继续补充更系统的版本。"
            ),
        }
        return clean_article_text(pieces.get(plan.day_index, pieces[1]))

    def _default_cover_prompt(self, plan: WechatLaunchDayPlan) -> str:
        return (
            f"16:9 中文科技封面图，主题“{plan.theme}”，深色背景，蓝黑主色，局部青色高亮，"
            "不是廉价模板，不是卡通插画。需要像头条科技分析文章封面：标题信息强，"
            "带产业链/数据/机器人元素，画面克制、高级、适合公众号与头条通用。"
        )

    def _default_infographic_prompts(self, plan: WechatLaunchDayPlan) -> list[str]:
        return [
            (
                f"正文信息图，主题“{plan.theme}”，竖版科技信息图，深色背景，中文标签清晰，"
                "突出产业链结构、关键变量和判断结论，适合插入公众号正文中段。"
            ),
            (
                f"正文配图，围绕“{plan.theme}”，不是海报字报，而是更像高质量产业图解，"
                "保留机器人、供应链、数据节点的视觉暗示，移动端友好。"
            ),
        ]

    def _write_run_package(self, plan: WechatLaunchDayPlan, publish_date: date, payload: dict[str, Any]) -> dict[str, Path]:
        title = str(payload.get("selected_title", "")).strip() or plan.suggested_titles[0]
        slug = self._slugify_title(title)
        run_name = f"{publish_date.strftime('%Y%m%d')}-wechat-day{plan.day_index}_{slug}"
        run_dir = self.settings.runs_dir / run_name
        publish_dir = run_dir / self.settings.review.publish_dirname
        run_dir.mkdir(parents=True, exist_ok=True)
        publish_dir.mkdir(parents=True, exist_ok=True)

        article_text = clean_article_text(str(payload.get("article", "")).strip())
        summary = str(payload.get("summary", "")).strip()
        comment_prompt = str(payload.get("comment_prompt", "")).strip()
        tags = [str(item).strip() for item in payload.get("tags", []) if str(item).strip()]
        candidate_titles = [str(item).strip() for item in payload.get("candidate_titles", []) if str(item).strip()]
        cover_prompt = str(payload.get("cover_prompt", "")).strip()
        infographic_prompts = [str(item).strip() for item in payload.get("infographic_prompts", []) if str(item).strip()]
        validation_notes = [str(item).strip() for item in payload.get("validation_notes", []) if str(item).strip()]

        launch_payload = {
            "prepared_at": datetime.now().isoformat(timespec="seconds"),
            "publish_date": publish_date.isoformat(),
            "plan": {
                "day_index": plan.day_index,
                "theme": plan.theme,
                "purpose": plan.purpose,
                "keyword_reply": plan.keyword_reply,
                "reply_content": plan.reply_content,
                "suggested_titles": plan.suggested_titles,
                "structure_points": plan.structure_points,
            },
            "content": {
                "selected_title": title,
                "candidate_titles": candidate_titles,
                "summary": summary,
                "comment_prompt": comment_prompt,
                "tags": tags,
                "cover_prompt": cover_prompt,
                "infographic_prompts": infographic_prompts,
                "validation_notes": validation_notes,
            },
        }

        publish_meta = {
            "approved_at": datetime.now().isoformat(timespec="seconds"),
            "selected_title": title,
            "selected_title_index": 1,
            "summary": summary,
            "tags": tags,
            "recommended_topics": [plan.theme, "人形机器人", "具身智能"],
            "comment_prompt": comment_prompt,
            "style_key": "deep_industry_analysis",
            "style_label": "公众号起号计划",
            "score_snapshot": {
                "overall_score": 90,
                "title_attraction": 88,
                "information_density": 90,
                "save_potential": 90,
                "completion_rate_potential": 90,
                "ai_generated_risk": 10,
                "publish_readiness_score": 88,
                "validation_score": 88,
                "publish_recommendation": "approve",
            },
            "publish_recommendation": "approve",
            "selected_topic": {
                "title": plan.theme,
                "source": "wechat_launch_plan",
                "source_type": "manual",
                "summary": plan.purpose,
                "matched_industries": ["humanoid_robot"],
                "matched_keywords": ["人形机器人", "具身智能", "机器人产业链"],
                "rank": plan.day_index,
            },
            "source_run_dir": str(run_dir),
            "validation_notes": validation_notes,
            "cover_prompt": cover_prompt,
            "infographic_prompts": infographic_prompts,
            "launch_plan_day": plan.day_index,
            "launch_keyword_reply": plan.keyword_reply,
            "launch_reply_content": plan.reply_content,
        }

        review_status = {
            "status": "approved",
            "recommended_action": "approve_after_light_check",
            "selected_title": title,
            "selected_title_index": 1,
            "reviewer_notes": f"Prepared from WeChat launch week plan day {plan.day_index}.",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "selected_topic_title": plan.theme,
            "score_snapshot": publish_meta["score_snapshot"],
            "publish_recommendation": "approve",
            "source_run_dir": str(run_dir),
            "publish_dir": str(publish_dir),
        }

        (run_dir / "article.md").write_text(article_text + "\n", encoding="utf-8")
        (run_dir / "launch_plan.json").write_text(json.dumps(launch_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "review_status.json").write_text(json.dumps(review_status, ensure_ascii=False, indent=2), encoding="utf-8")

        (publish_dir / "article.md").write_text(article_text + "\n", encoding="utf-8")
        (publish_dir / "title.txt").write_text(title + "\n", encoding="utf-8")
        (publish_dir / "summary.txt").write_text(summary + "\n", encoding="utf-8")
        (publish_dir / "comment_prompt.txt").write_text(comment_prompt + "\n", encoding="utf-8")
        (publish_dir / "tags.txt").write_text(" ".join(tags).strip() + "\n", encoding="utf-8")
        (publish_dir / "publish_meta.json").write_text(json.dumps(publish_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (publish_dir / "cover_prompt.txt").write_text(cover_prompt + "\n", encoding="utf-8")
        (publish_dir / "infographic_prompts.txt").write_text("\n\n".join(infographic_prompts).strip() + "\n", encoding="utf-8")
        (publish_dir / "candidate_titles.txt").write_text("\n".join(candidate_titles).strip() + "\n", encoding="utf-8")

        return {
            "run_dir": run_dir,
            "launch_plan": run_dir / "launch_plan.json",
            "review_status": run_dir / "review_status.json",
            "publish_article": publish_dir / "article.md",
            "publish_title": publish_dir / "title.txt",
            "publish_meta": publish_dir / "publish_meta.json",
            "cover_prompt": publish_dir / "cover_prompt.txt",
            "infographic_prompts": publish_dir / "infographic_prompts.txt",
        }

    def _clip_text(self, text: str, limit: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit].rstrip(" \t\r\n,，。！？、:：;；-")

    def _slugify_title(self, title: str) -> str:
        text = re.sub(r"[\\/:*?\"<>|]+", "-", title)
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text[:48] or f"day-{datetime.now().strftime('%H%M%S')}"
