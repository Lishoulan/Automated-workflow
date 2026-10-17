"""First-screen copy is real post content, not a detached headline plus recap."""
import json
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


def make_topic():
    item = TopicCandidate(title="赠房份额纠纷", url="https://court.example/report", source="法院公开材料", source_type="test",
        snippet="法院支持撤销陆奶奶本人转出的房产份额。其他共有人转出的份额未在本案中处理。协议中记载了赡养义务，报道没有披露其他房屋的处置结果。")
    item._verified_source_material = True
    item._content_experiment = {"id": "story_opening_v1", "variant": "result_first", "protocol_version": 4}
    return item


def article(lead):
    return (lead + "\n\n撤销自己给出的份额，与收回其他人的份额，是两个不同问题。家人在同一次安排中转出财产，也不意味着其中一人就能处理所有人的份额。"
        "\n\n本案能看清的是赠与关系与约定履行之间的联系。不能只看到亲属之间发生争议，就跳过具体约定，把结果扩大到所有家庭财产。"
        "\n\n公开材料给出的范围有限。理解这个结果时，应把已经得到支持的请求与没有进入本案的事项分开；后者需要独立的事实依据，不能自行补成法院的结论。\n\n#家庭财产")


GOOD = "法院支持陆奶奶撤销本人转出的赠房份额。其他共有人转出的份额，未在本案中处理。"


def test_actual_detached_headline_cannot_use_new_feed_protocol(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    content = article("陆奶奶的赠房份额被撤销\n\n陆奶奶将老宅拆迁份额赠与孙女，但孙女未履行赡养义务。法院最终撤销了赠与。")
    assert service._model_story_fallback_reason(make_topic(), "story_comment", content) == "story_detached_headline_guard"


def test_archived_single_newline_headline_cannot_hide_inside_first_paragraph(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    content = article("陆奶奶的赠房份额被撤销\n陆奶奶将老宅拆迁份额赠与孙女，但孙女未履行赡养义务。法院最终撤销了赠与。")
    assert service._feed_opening_reason(content) == "story_detached_headline_guard"


@pytest.mark.parametrize("lead,reason", [
    (GOOD, ""),
    ("法院支持陆奶奶撤销本人转出的赠房份额。", "story_feed_lead_guard"),
    ("法院支持陆奶奶撤销本人转出的赠房份额。法院支持陆奶奶撤销本人转出的赠房份额。", "story_opening_repeat_guard"),
    (GOOD + "不能扩展到全部房产。", "story_feed_lead_guard"),
    ("本案处理的是争议当事人自己已经转出并且经过法院核对的财产份额，这与其他家庭成员独立作出的财产安排涉及不同的处理范围，需要结合具体的申请事项逐一理解，不能仅因当事人存在亲属关系就把这些财产全部混在一起。这一边界不应被省略。", "story_opening_length_guard"),
])
def test_first_paragraph_has_two_short_factual_sentences_with_the_qualification(app_settings, logger, lead, reason):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    assert service._model_story_fallback_reason(make_topic(), "story_comment", article(lead)) == reason


def test_two_existing_model_calls_request_continuous_copy_not_a_title_field(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    service.openai_service.create_text_completion = MagicMock(return_value=article(GOOD))
    item = make_topic()
    result = service.generate_content(item, "story_comment")
    calls = service.openai_service.create_text_completion.call_args_list
    assert len(calls) == 2 and all(call.kwargs["max_tokens"] == 900 for call in calls)
    assert "不要单列标题" in calls[0].kwargs["user_prompt"]
    assert "连续首段" in calls[1].kwargs["system_prompt"]
    assert "标题加250" not in calls[1].kwargs["system_prompt"]
    assert item._content_experiment["protocol_version"] == 4
    assert result == article(GOOD)


def test_protocol4_does_not_slice_reviewed_text_and_rejects_overlong_output(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    paragraphs = article(GOOD).split("\n\n")
    reviewed = "\n\n".join([paragraphs[0], *paragraphs[1:-1] * 4, paragraphs[-1]])
    assert len(reviewed) > 700
    service.openai_service.create_text_completion = MagicMock(side_effect=[article(GOOD), reviewed])
    item = make_topic()
    result = service.generate_content(item, "story_comment")
    assert result == reviewed
    assert service._model_story_fallback_reason(item, "story_comment", result) == "story_total_length_guard"
    assert service.openai_service.create_text_completion.call_count == 2


def test_full_lead_is_counted_in_source_copy_detection(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = make_topic()
    copied = "法院支持撤销本人赠与的份额，但其他共同所有人的份额没有在本案处理。" * 9
    item.snippet = copied
    # A single pasted paragraph used to be mistaken for the excluded headline.
    assert service._source_copy_share(item, copied) >= .8


def test_feed_qualification_survives_package_and_publisher_sanitization(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = make_topic()
    content = article(GOOD)
    assert not service._model_story_fallback_reason(item, "story_comment", content)
    run = service.build_run_package(item, content, "story_comment")
    final = service.publish_service._sanitize_article_for_publish(
        (run / "publish/article.md").read_text(encoding="utf-8"), preserve_sentence_endings=True)
    assert "未在本案中处理" in final and "其他共有人" in final
    assert final.split("\n\n")[0] == GOOD
    metadata = json.loads((run / "publish/publish_meta.json").read_text(encoding="utf-8"))
    assert metadata["content_experiment"]["protocol_version"] == 4


def test_submission_ledger_keeps_actual_opening_instead_of_clipped_display_title(app_settings, logger):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    run = service.build_run_package(make_topic(), article(GOOD), "story_comment")
    service._write_publish_result_artifact(run, {"status": "submitted_for_review",
        "published_at": "2026-10-16T09:00:07+08:00", "submitted_opening": GOOD})
    ledger = json.loads((app_settings.output_dir / "growth_ops/performance/wtt_content_experiments.json").read_text(encoding="utf-8"))
    assert ledger["records"][run.name]["submitted_opening"] == GOOD


def test_micro_publication_selects_the_continuous_sentence_cleaning_boundary(app_settings, logger, monkeypatch):
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    run = service.build_run_package(make_topic(), article(GOOD), "story_comment")
    stop = RuntimeError("stop before any browser or publication")
    clean = MagicMock(side_effect=stop)
    monkeypatch.setattr(service.publish_service, "_sanitize_article_for_publish", clean)
    with pytest.raises(RuntimeError, match="stop before"):
        service.publish_service.publish_weitoutiao(run)
    assert clean.call_args.kwargs["preserve_sentence_endings"] is True


def test_invalid_lead_is_skipped_without_rewriting_or_another_model_call(app_settings, logger):
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = make_topic()
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    service._iter_eligible_topics = MagicMock(return_value=[item])
    service.openai_service.create_text_completion = MagicMock(return_value=article("陆奶奶的赠房份额被撤销"))
    service.publish_service.publish_weitoutiao = MagicMock()
    result = service.publish_standalone(count=1)
    assert result["accepted"] == 0
    assert service.openai_service.create_text_completion.call_count == 2
    service.publish_service.publish_weitoutiao.assert_not_called()


def test_protocol_four_does_not_absorb_the_previous_protocol_three():
    from toutiao_content_factory.services.growth_feedback_service import opening_experiment_summary
    works = {str(i): {"type": "weitoutiao", "content_experiment": {"id": "story_opening_v1", "variant": "result_first", "protocol_version": 3},
                      "checkpoints": {"24h": {"impressions": 100, "reads": 4, "capture_delay_hours": 0}}} for i in range(15)}
    assert opening_experiment_summary({"works": works})["protocol_version"] == 3
    works["new"] = {"type": "weitoutiao", "content_experiment": {"id": "story_opening_v1", "variant": "result_first", "protocol_version": 4},
                    "checkpoints": {"24h": {"impressions": 100, "reads": 3, "capture_delay_hours": 0}}}
    report = opening_experiment_summary({"works": works})
    assert report["protocol_version"] == 4 and report["legacy_works_excluded"] == 15
    assert sum(row["samples"] for row in report["groups"]) == 1


@pytest.mark.parametrize("prefix,blocked", [("", True), ("案例回顾：", True), ("案例回顾：2021年公开的这起案件中，", False)])
def test_court_case_cannot_look_like_fresh_news_or_lose_its_verified_report_year(app_settings, logger, prefix, blocked):
    service = WeiToutiaoStandaloneGenerator(app_settings, logger)
    item = make_topic()
    item.source_type = "court_case"
    item.published_at = "2021-10-20T19:02:01"
    item.snippet = "【原文发布日期：2021年10月20日】\n" + item.snippet
    reason = service._model_story_fallback_reason(item, "story_comment", article(prefix + GOOD))
    assert bool(reason) is blocked
    if blocked:
        assert reason == "story_case_date_guard"
