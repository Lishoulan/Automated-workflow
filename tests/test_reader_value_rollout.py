"""Concrete reader value enters selection and writing without buying more calls."""
import json
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


def topic(title, **kwargs):
    return TopicCandidate(title=title, url="https://source.test/report", source="rss", source_type="rss", **kwargs)


@pytest.mark.parametrize("title", ["女儿体检身高获夸赞", "外国游客体验机器人科技", "男子高铁座位上排便",
    "黄磊女儿继承妈妈美貌，晒出生日写真", "老人体验新款机器人加班赶订单，展示最新动作技术"])
def test_story_selection_filters_incidental_audience_words_before_retrieval(app_settings, logger, title):
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    irrelevant = topic(title, matched_keywords=["老人", "房产", "工资"], rank=1)
    relevant = topic("业主索要停车费账本，法院明确公开范围", rank=20)
    service.topic_automation._load_daily_topics = MagicMock(return_value=MagicMock(topics=[irrelevant, relevant]))
    service.topic_automation._available_topics = MagicMock(side_effect=lambda rows: rows)
    service.topic_automation.topic_pool_service.build_fallback_report = MagicMock(return_value=MagicMock(topics=[]))
    service.topic_automation._hydrate_topic_material = MagicMock(return_value=True)

    assert service.select_topics(count=1) == [relevant]
    assert service.topic_automation._hydrate_topic_material.call_args.args[0] is relevant
    assert (irrelevant.rank, relevant.rank) == (1, 20)


@pytest.mark.parametrize("title", ["黄磊女儿继承妈妈美貌，晒出生日写真", "老人体验新款机器人加班赶订单，展示最新动作技术"])
def test_actual_account_filter_cannot_make_figurative_track_markers_relevant(title):
    from toutiao_content_factory.services.topic_collection import TopicCollectionService
    from toutiao_content_factory.services.reader_value_service import story_reader_fit
    item = topic(title)
    assert TopicCollectionService._has_social_story_signal(item)
    assert story_reader_fit(item)["eligible"] is False


@pytest.mark.parametrize("title", ["老人赠房后孙女未履行赡养约定", "工人工资被拖欠，调解后收到款项", "健身房闭店，会员申请退费", "养老院护理费争议，家属核对合同", "社区志愿者帮老人找到走失家人",
    "子女继承父母遗产，法院明确处理范围", "员工拒绝加班被辞退，法院认定违法解除", "职工申请加班费，单位提供考勤记录"])
def test_story_focus_keeps_concrete_practical_topics(title):
    from toutiao_content_factory.services.reader_value_service import story_reader_fit
    report = story_reader_fit(topic(title))
    assert report["eligible"] is True and report["tracks"]
    assert report["basis"] == "editorial_choice"


def test_story_focus_does_not_filter_other_accounts(app_settings, logger):
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["hot_comment"])
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    items = [topic("机器人芯片进入测试", rank=1), topic("苹果市值排名更新", rank=2)]
    assert service._rank_topics(items) == items


def test_source_ready_story_is_not_displaced_by_an_unretrieved_hot_title(app_settings, logger):
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    hot = topic("业主停车费争议", rank=1)
    ready = topic("工资争议有调解记录", rank=20)
    ready._verified_source_material = True
    ready._material_evidence_assessment = {"passed": True}
    assert service._rank_topics([hot, ready]) == [ready, hot]


def test_other_accounts_keep_their_existing_feedback_order(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    service._load_growth_feedback = lambda: {"topic_feedback": {"weights": {"房产继承": 18}}}
    boosted = topic("房产继承处理范围", rank=20)
    ready = topic("芯片测试结果", rank=1)
    ready._verified_source_material = True
    ready._material_evidence_assessment = {"passed": True}
    assert service._rank_topics([ready, boosted]) == [boosted, ready]


def test_source_readiness_does_not_revive_an_explicitly_stopped_story(app_settings, logger):
    from toutiao_content_factory.services.growth_feedback_service import normalize_title
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    stopped, fresh = topic("旧停车费纠纷"), topic("会员退费争议")
    stopped._verified_source_material = True
    stopped._material_evidence_assessment = {"passed": True}
    service._load_growth_feedback = lambda: {"topic_feedback": {"stop_titles": [normalize_title(stopped.title)]}}
    assert service._rank_topics([stopped, fresh]) == [fresh]


@pytest.mark.parametrize("title,policy", [
    ("老人入住养老院后，孙女未履行赡养约定", False),
    ("退休老人赠房后发生继承纠纷", False),
    ("公司请假制度引起劳动争议", False),
    ("农民交公粮能否视同缴社保", True),
    ("“看病请人陪”有国家标准了", True),
    ("我国基本养老保险参保人数公布", True),
    ("养老补贴政策征求意见", True),
])
def test_care_cases_retain_fact_review_without_becoming_policy_approval(app_settings, logger, title, policy):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = topic(title)
    assert service._is_public_policy_topic(item, "story_comment") is policy
    if policy:
        assert service._model_story_fallback_reason(item, "story_comment", "尚无可靠解释。") == "policy_story_evidence_guard"
        assert service.build_local_fallback_content(item, "story_comment") == ""


def test_wechat_equal_evidence_prefers_explainable_product_over_market_cap():
    def rich(title, text):
        item = topic(title)
        item.source_records = [{"source_id": "src_1", "url": item.url, "title": title,
            "original_text": "。".join(text.format(i=i) for i in range(60))}]
        return item
    market = rich("苹果市值排名创新高", "第{i}个交易日，苹果市值达到{i}万亿元，股票收盘价格披露后排名发生变化")
    product = rich("机器人完成工位测试，披露操作条件", "公司第{i}项机器人设备测试在固定工位完成，设备部署达到{i}台，公开了操作条件和验证范围")
    ranked = sorted([market, product], key=ContentFactoryAppService._wechat_candidate_sort_key)
    assert ranked == [product, market]


def test_reader_brief_uses_distinct_verbatim_quotes_without_inventing_an_answer():
    from toutiao_content_factory.services.reader_value_service import build_reader_brief
    quotes = ["法院支持撤销老人本人转出的房产份额。", "其他共有人转出的份额未在本案中处理。"]
    brief = build_reader_brief("老人赠房纠纷", quotes + quotes, profile="social_practical")
    assert brief["source_quotes"] == quotes
    assert brief["question"] and brief["answer_verified"] is False
    assert "全部房产" not in json.dumps(brief, ensure_ascii=False)


def padding():
    return ("普通读者可以先保存权威来源，再根据后续公开进展更新判断。\n\n"
            "这里需要把已经发生的事实、网友观点和仍待确认的信息分开。\n\n"
            "对普通读者来说，最稳妥的做法是记录事实、标注推测，并随着新信息更新结论。")


def test_known_generic_padding_is_rejected_at_the_final_story_boundary(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = topic("停车费公开范围")
    assert service._model_story_fallback_reason(item, "story_comment", "停车费争议\n\n" + padding()) == "story_reader_value_guard"
    assert service._model_story_fallback_reason(item, "story_comment",
        "业主索要停车费账本，法院明确公开范围\n\n业主要求查阅停车收费和支出记录。法院支持公开共有收益的账目，未把车位产权争议纳入本案。\n\n"
        "费用记录和产权归属是两个问题。账目能解释收支去向，却不能凭这次公开账目就认定每个车位归谁。") == ""


def test_actual_policy_template_is_detected_without_discarding_real_explanations():
    from toutiao_content_factory.services.reader_value_service import generic_padding_reason
    actual = ("“看病请人陪”有国家标准了\n\n"
        "根据公开材料目前公开的信息，可以确认的是：“看病请人陪”有国家标准了 规范服务解老人难题_中华网。"
        "这里需要把已经发生的事实、网友观点和仍待确认的信息分开。\n\n"
        "如果话题涉及政策、社保、养老或公共待遇，还要继续核对适用地区、生效时间和主管部门正式文件。"
        "公开讨论不等于规则已经改变，没有明确人物和处理结果时，也不补写具体人物的经历。\n\n"
        "普通读者可以先保存权威来源，再根据后续公开进展更新判断。"
        "这件事里，你最希望主管部门先说明哪一个具体问题？")
    assert generic_padding_reason(actual) == "known_generic_padding"


@pytest.mark.parametrize("summary", ["本文深入探讨行业趋势，带你洞察未来发展。", "这款机器人已经实现全面盈利。", ""])
def test_wechat_summary_comes_from_complete_body_context_without_inventing_claims(summary):
    from toutiao_content_factory.services.reader_value_service import grounded_summary
    paragraph = "公司披露机器人在固定工位完成测试，尚未公布商业订单。测试通过不能直接等同于实现盈利。"
    article = "# 机器人测试\n\n" + paragraph + "\n\n参考来源：\n《报道》 https://source.test/report"
    assert grounded_summary(summary, article) == paragraph


def test_summary_does_not_cut_a_qualification_or_force_an_oversized_extract():
    from toutiao_content_factory.services.reader_value_service import grounded_summary
    paragraph = "网上出现该公司已经盈利的说法。但是公告只披露固定工位测试，未披露盈利。"
    assert grounded_summary("网上出现该公司已经盈利的说法。", paragraph) == paragraph
    assert grounded_summary("", "数据解释" * 50 + "，不能推出已经盈利。") == ""
    assert grounded_summary("", "参考来源：\nhttps://source.test/report") == ""


def test_writer_uses_a_real_fact_brief_and_a_grounded_summary_without_an_extra_call(app_settings, logger):
    from toutiao_content_factory.agents.writer_agent import WriterAgent
    from toutiao_content_factory.models import DataReport, TrendReport, CacheContext
    from toutiao_content_factory.services.openai_service import OpenAIContentService
    from toutiao_content_factory.services import wechat_evidence as evidence
    from toutiao_content_factory.services.material_search_service import MaterialSource
    text = "陕西发布储能通知，要求相关储能项目与配套新能源项目同期投产。适用条件按通知执行。"
    records = evidence.source_records([MaterialSource("陕西储能通知", "https://source.test/report", text, True)])
    data = DataReport(topic_title="陕西储能通知", source_records=records,
        fact_evidence=evidence.build_facts(records), article_kind="policy_explainer")
    writer = WriterAgent(app_settings, MagicMock(), OpenAIContentService(app_settings, logger), logger)
    writer.request_json = MagicMock(return_value={"article": text, "summary": "本文深入探讨行业趋势。", "tags": []})
    generated = writer._write_wechat(TrendReport(), data, app_settings.get_style(), CacheContext())
    assert "行业趋势" not in generated.summary and "同期投产" in generated.summary
    assert generated.grounding_diagnostics[0]["reader_value_brief"]["source_quotes"]
    assert generated.grounding_diagnostics[0]["reader_value_brief"]["answer_verified"] is False
    assert writer.request_json.call_count == 1


def test_verified_story_package_keeps_its_source_specific_writing_brief(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = topic("业主查停车费账本")
    item._verified_source_material = True
    item.snippet = "业主要求查阅停车收费和支出记录。法院支持公开共有收益的账目，未把车位产权争议纳入本案。"
    content = "法院明确停车收费账目公开范围\n\n" + "公开记录说明了这次账目公开的范围。" * 15
    service.openai_service.create_text_completion = MagicMock(return_value=content)
    result = service.generate_content(item, "story_comment")
    path = service.build_run_package(item, result, "story_comment")
    meta = json.loads((path / "publish/publish_meta.json").read_text(encoding="utf-8"))
    assert meta["reader_value_brief"]["source_quotes"] == [
        "业主要求查阅停车收费和支出记录。", "法院支持公开共有收益的账目，未把车位产权争议纳入本案。"]
    assert meta["reader_value_brief"]["answer_verified"] is False
    assert service.openai_service.create_text_completion.call_count == 2
