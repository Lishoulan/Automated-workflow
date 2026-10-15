"""Reader-facing opening and source regressions from the October 12 cloud run."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.material_search_service import extract_article_body
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


def generator(settings, logger):
    return WeiToutiaoStandaloneGenerator(settings, logger)


@pytest.mark.parametrize("prefix", ["标题：", "标题: ", "**标题：", "Title: "])
def test_headline_survives_generation_package_and_publish_cleaning(app_settings, logger, prefix):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="工地只建5个棚，承诺尚未兑现", url="https://source.test/report", source="report", source_type="test")
    headline = "工地只建5个棚，承诺尚未兑现"
    content = prefix + headline + ("**" if prefix.startswith("**") else "") + "\n\n" + "素材中已确认的事实与判断边界。" * 20
    service.openai_service.create_text_completion = MagicMock(return_value=content)
    generated = service.generate_content(topic, "hot_comment")
    run = service.build_run_package(topic, generated, "hot_comment")
    final = service.publish_service._sanitize_article_for_publish((run / "publish/article.md").read_text(encoding="utf-8"))
    assert final.splitlines()[0] == headline
    meta = json.loads((run / "publish/publish_meta.json").read_text(encoding="utf-8"))
    assert meta["selected_title"] == headline


def test_primary_china_news_container_excludes_other_headlines_and_numbers():
    body = "本项目公布建设5个棚，现场尚未完工，施工人员正在等待后续处理。" * 10
    page = f'<body><div id="chan_newsDetail"><p>{body}</p><div><p>现场情况仍需继续核验。</p></div></div><div><p>{"其他项目有7个棚，金额为99亿元。" * 20}</p></div></body>'
    extracted = extract_article_body(page, min_chars=100)
    assert body in extracted
    assert "7个棚" not in extracted and "99亿元" not in extracted
    short = '<div id="chan_newsDetail"><p>本案只有简短通报。</p></div><div><p>' + body + '</p></div>'
    assert extract_article_body(short, min_chars=100) == ""
    assert extract_article_body('<div id="chan_newsDetail"><p>' + body, min_chars=100) == ""


@pytest.mark.parametrize("content,blocked", [
    ("仅建7个棚，项目仍未完工。", True),
    ("材料称建设5个棚，不能当作全部竣工。", False),
    ("项目金额1.2亿，施工队尚未结算。", False),
    ("项目金额1.3亿，施工队尚未结算。", True),
])
def test_unsupported_story_measurements_do_not_reach_publication(app_settings, logger, content, blocked):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="项目调查", url="https://source.test/report", source="report", source_type="test",
        snippet="公开材料称该项目金额1.2亿元，现场建设5个棚，成立9天，注册资金550万元。")
    topic._verified_source_material = True
    reason = service._model_story_fallback_reason(topic, "story_comment", content)
    assert bool(reason) is blocked
    if blocked:
        assert reason == "story_number_evidence_guard"


def test_story_prompt_explains_the_actual_feed_opening_and_keeps_one_review(app_settings, logger):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="项目未完工", url="https://source.test/report", source="report", source_type="test", snippet="项目尚未完工，施工方仍在等待处理。" * 20)
    topic._verified_source_material = True
    service.openai_service.create_text_completion = MagicMock(return_value="项目尚未完工\n\n" + "公开素材能确认的事实与分析边界。" * 20)
    service.generate_content(topic, "story_comment")
    calls = service.openai_service.create_text_completion.call_args_list
    assert len(calls) == 2
    assert "信息流首屏" in calls[0].kwargs["user_prompt"]
    assert "40字" in calls[0].kwargs["user_prompt"]
    assert "不替司法机关认定诈骗" in calls[1].kwargs["system_prompt"]


def test_people_and_money_are_not_interchangeable(app_settings, logger):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="公开统计", url="https://source.test/report", source="report", source_type="test",
        snippet="公开统计参与人数2万人，款项2万元。")
    topic._verified_source_material = True
    assert not service._model_story_fallback_reason(topic, "story_comment", "参与人数20000人，款项20000元。")
    assert service._model_story_fallback_reason(topic, "story_comment", "款项20000万元。") == "story_number_evidence_guard"


def test_new_opening_protocol_does_not_compare_against_pre_fix_posts(app_settings, logger):
    from toutiao_content_factory.services.growth_feedback_service import opening_experiment_summary
    service = generator(app_settings, logger)
    assignment = service._opening_experiment()
    assert assignment["protocol_version"] == 3
    works = {}
    for index in range(30):
        works[str(index)] = {"type": "weitoutiao", "content_experiment": {"id": "story_opening_v1",
            "variant": "result_first" if index < 15 else "situation_first", "protocol_version": 2},
            "checkpoints": {"24h": {"impressions": 100, "reads": 20, "capture_delay_hours": 0}}}
    assert opening_experiment_summary({"works": works})["status"] == "comparison_ready"
    assert opening_experiment_summary({"works": works})["protocol_version"] == 2
    works["new"] = {"type": "weitoutiao", "content_experiment": assignment,
        "checkpoints": {"24h": {"impressions": 100, "reads": 3, "capture_delay_hours": 0}}}
    report = opening_experiment_summary({"works": works})
    assert report["status"] == "collecting" and report["protocol_version"] == 3
    assert sum(group["samples"] for group in report["groups"]) == 1
    assert report["legacy_works_excluded"] == 30


@pytest.mark.parametrize("wechat_mode", [False, True])
def test_main_content_excludes_recommendations_and_cannot_inflate_material_evidence(wechat_mode):
    from toutiao_content_factory.services.material_search_service import MaterialBundle, MaterialSource, assess_material_evidence
    article = "当事人公开了一次常规检查的经过，医生回应了她的问题，未提供其他诊疗信息。" * 12
    sidebar = "其他新闻涉及7个棚、9亿元投资和另一人的健康争议，不能用于本案。" * 30
    page = f'<body><div id="main-content"><p>{article}</p><div><p>公开记录尚无后续变化。</p></div></div><div id="article-recommend"><p>{sidebar}</p></div></body>'
    body = extract_article_body(page, min_chars=100, wechat_mode=wechat_mode)
    assert article in body and "其他新闻" not in body and "9亿元" not in body
    bundle = MaterialBundle("公开记录", [MaterialSource("公开记录", "https://kan.china.com/article/example.html", body, True)])
    assert not assess_material_evidence(bundle).passed  # Only ~400 real characters, not a 900-character article.
    assert extract_article_body('<div id="main-content"><p>' + article, min_chars=100, wechat_mode=wechat_mode) == ""


@pytest.mark.parametrize("advice", [
    "家长需要关注孩子的骨龄发育情况。",
    "特殊基因组合要防过度生长。",
    "父母应该监测儿童的身高变化。",
])
def test_story_does_not_turn_a_family_checkup_into_health_advice(app_settings, logger, advice):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="公开体检记录", url="", source="公开报道", source_type="test",
        snippet="家长分享常规检查经过，医生表示本次检查未发现异常。")
    assert service._model_story_fallback_reason(topic, "story_comment", advice) == "story_health_advice_guard"
    assert not service._model_story_fallback_reason(topic, "story_comment", "家长表示担心身高，医生称本次检查正常。该记录不能推出其他孩子的发育状况。")


def _mobile_story():
    return ("老人获返还房产份额，法院明确撤销范围\n\n"
            "这起纠纷涉及老人此前转出的房产份额。公开裁判明确了本人的请求范围，其他人的权利并未在本案中一并处理。\n\n"
            "判断此案，需要先分清提出请求的人和争议中的财产。新闻里的家庭关系说明了争议背景，却不能直接扩大裁判处理的范围。\n\n"
            "一个值得注意的区别是，提出请求和获得支持是两件事。标题说清已确认的结果，正文还需要交代这个结果实际处理到了哪一步。\n\n"
            "从已披露的条件看，读者可以理解这次纠纷的处理边界。其他家庭是否适用同样处理，仍取决于各自的事实，不能只凭关系相似就下结论。")


@pytest.mark.parametrize("defect,reason", [
    ("long_opening", "story_opening_length_guard"),
    ("repeat_opening", "story_opening_repeat_guard"),
    ("wall_of_text", "story_paragraph_length_guard"),
])
def test_reviewed_story_must_still_meet_mobile_reading_limits(app_settings, logger, defect, reason):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="房产份额纠纷", url="", source="公开报道", source_type="test", snippet="公开报道说明了裁判处理的范围。")
    topic._verified_source_material = True
    valid = _mobile_story()
    assert not service._model_story_fallback_reason(topic, "story_comment", valid)
    lines = valid.splitlines()
    if defect == "long_opening":
        lines[0] = "老人此前转出房产份额后产生争议，法院明确此次撤销涉及本人份额，其他人的财产权利尚未在本案中处理"
    elif defect == "repeat_opening":
        lines[2] = lines[0] + "。" + lines[2]
    else:
        lines = [lines[0], "", "".join(lines[1:])]
    assert service._model_story_fallback_reason(topic, "story_comment", "\n".join(lines)) == reason


def test_failed_mobile_check_skips_without_another_model_call_or_publication(app_settings, logger):
    from dataclasses import replace
    service = generator(app_settings, logger)
    app_settings.publishing = replace(app_settings.publishing, weitoutiao_enabled=True, weitoutiao_personas=["story_comment"])
    topic = TopicCandidate(title="房产份额纠纷", url="", source="公开报道", source_type="test", snippet="裁判处理本人份额。")
    topic._verified_source_material = True
    bad = "第一行堆满了案情和多个处置细节，既没有控制信息量，也把所有发生经过都放在读者看到的第一行里\n\n" + _mobile_story().split("\n\n", 1)[1]
    service._iter_eligible_topics = MagicMock(return_value=[topic])
    service.openai_service.create_text_completion = MagicMock(return_value=bad)
    service.publish_service.publish_weitoutiao = MagicMock()
    result = service.publish_standalone(count=1)
    service.publish_service.publish_weitoutiao.assert_not_called()
    assert service.openai_service.create_text_completion.call_count == 2
    assert result["skipped"][0]["generation_fallback_reason"] == "story_opening_length_guard"


@pytest.mark.parametrize("code", ["baidu_hot", "toutiao_hot", "hot_board"])
def test_collector_ids_do_not_appear_in_public_prose_or_replace_real_source_urls(code):
    from toutiao_content_factory.services.article_sanitizer import strip_internal_source_labels, clean_article_text
    url = f"https://source.test/{code}/report?ref={code}"
    raw = f"根据{code}公开材料，项目建设5个棚。新华社披露了相关进展。\n\n参考来源：\n《项目报道》；新华社；2026-10-15；{url}"
    clean = clean_article_text(raw, min_word_count=0)
    assert f"根据{code}" not in clean
    assert "项目建设5个棚" in clean and "新华社" in clean
    assert url in clean
    assert clean_article_text(clean, min_word_count=0) == clean
    assert strip_internal_source_labels("my_baidu_hot_v2 与 NVIDIA_AI 是技术标识。") == "my_baidu_hot_v2 与 NVIDIA_AI 是技术标识。"


def test_local_policy_fallback_and_model_source_hint_do_not_expose_collector_id(app_settings, logger):
    service = generator(app_settings, logger)
    topic = TopicCandidate(title="养老金公开政策信息", url="", source="baidu_hot", source_type="baidu_hot",
        summary="相关信息仍需核对适用范围和正式文件。")
    fallback = service.build_local_fallback_content(topic, "story_comment")
    assert "baidu_hot" not in fallback and "正式文件" in fallback
    service.openai_service.create_text_completion = MagicMock(return_value="养老金政策需要核对适用范围。")
    service.generate_content(topic, "hot_comment")
    assert "baidu_hot" not in service.openai_service.create_text_completion.call_args.kwargs["user_prompt"]
    assert topic.source == topic.source_type == "baidu_hot"  # Internal provenance remains available.
