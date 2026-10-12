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
    assert assignment["protocol_version"] == 2
    works = {}
    for index in range(30):
        works[str(index)] = {"type": "weitoutiao", "content_experiment": {"id": "story_opening_v1",
            "variant": "result_first" if index < 15 else "situation_first"},
            "checkpoints": {"24h": {"impressions": 100, "reads": 20, "capture_delay_hours": 0}}}
    assert opening_experiment_summary({"works": works})["status"] == "comparison_ready"
    works["new"] = {"type": "weitoutiao", "content_experiment": assignment,
        "checkpoints": {"24h": {"impressions": 100, "reads": 3, "capture_delay_hours": 0}}}
    report = opening_experiment_summary({"works": works})
    assert report["status"] == "collecting" and report["protocol_version"] == 2
    assert sum(group["samples"] for group in report["groups"]) == 1
    assert report["legacy_works_excluded"] == 30
