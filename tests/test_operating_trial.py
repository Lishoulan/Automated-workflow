from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.config import RevenueOptimizationSettings
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services import generation_cost_service as costs
from toutiao_content_factory.services import revenue_feedback as policy
from toutiao_content_factory.services.reader_value_service import generic_padding_reason

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone(timedelta(hours=8)))
TRIAL = {"id": "robot_notes_profit_20261018", "started_at": "2026-10-18T00:00:00+08:00",
         "directions": ["consumer_practical", "technology_everyday"], "free_cover_only": True}


def enabled(settings):
    settings.revenue_optimization = RevenueOptimizationSettings.from_dict(
        {"adaptive_topic_selection": True, "operating_trial": TRIAL})
    settings.account_key = "robot_notes"
    return settings


def topic(title, summary=""):
    return TopicCandidate(title, "https://source.example/" + str(abs(hash(title))), "原始报道", "news",
                          summary=summary, quality_score=80, evidence_strength=80)


def test_trial_only_admits_the_two_reader_value_directions(app_settings):
    settings = enabled(app_settings)
    topics = [topic("明星恋情引热议"), topic("消费者申请退款，商家公布服务与收费记录"),
              topic("手机厂商公布系统更新期限，旧手机用户何时停止获支持"),
              topic("芯片公司市值再创新高，投资者热议")]
    ranked = policy.rank_revenue_topics(topics, settings, {}, content_type="weitoutiao", now=NOW)
    assert {t.title for t in ranked} == {topics[1].title, topics[2].title}
    assert {t.revenue_selection["category"] for t in ranked} == set(TRIAL["directions"])
    assert all(t.revenue_selection["trial_id"] == TRIAL["id"] for t in ranked)
    assert all(not getattr(t, "_verified_source_material", False) for t in ranked)


def test_trial_prefers_underrepresented_admissions_but_keeps_alternate_fallback(app_settings):
    settings = enabled(app_settings)
    path = settings.output_dir / "growth_ops/performance/wtt_content_experiments.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"records": {"first": {"id": "story_opening_v1", "protocol_version": 4,
        "trial_id": TRIAL["id"], "selection_protocol": policy.SELECTION_PROTOCOL,
        "category": "consumer_practical", "status": "submitted_for_review"}}}), encoding="utf-8")
    consumers = [topic(f"消费者退款服务记录{i}") for i in range(10)]
    tech = topic("手机系统停止更新，老用户还能获得安全补丁吗")
    ranked = policy.rank_revenue_topics([*consumers, tech], settings, {}, content_type="weitoutiao", now=NOW)
    assert ranked[0] is tech
    assert ranked[1] in consumers  # a bounded retrieval can still reach the other direction


def test_corrupt_admission_history_does_not_reset_trial_counts(app_settings):
    settings = enabled(app_settings)
    path = settings.output_dir / "growth_ops/performance/wtt_content_experiments.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="admission"):
        policy.rank_revenue_topics([topic("消费者退款")], settings, {}, content_type="weitoutiao", now=NOW)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_trial_defaults_do_not_restrict_other_accounts_or_wechat(app_settings):
    settings = enabled(app_settings)
    for account, platform in (("another", "今日头条"), ("robot_notes", "wechat")):
        settings.account_key, settings.platform = account, platform
        candidates = [topic("原始来源的产品实测")]
        result = policy.rank_revenue_topics(candidates, settings, {}, content_type="weitoutiao", now=NOW)
        assert len(result) == 1
        assert "trial_id" not in result[0].revenue_selection


@pytest.mark.parametrize("when", [datetime(2026, 10, 17, 23, 59, tzinfo=NOW.tzinfo),
                                  datetime(2026, 11, 17, 0, 0, 1, tzinfo=NOW.tzinfo)])
def test_trial_does_not_filter_or_tag_work_outside_enrollment(app_settings, when):
    settings = enabled(app_settings)
    candidates = [topic("法院审理继承纠纷，明确共有份额"), topic("消费者申请退款，核对交付记录")]
    ranked = policy.rank_revenue_topics(candidates, settings, {}, content_type="weitoutiao", now=when)
    assert len(ranked) == 2
    assert all("trial_id" not in item.revenue_selection for item in ranked)
    # The configured spec remains available for closing the report.
    assert policy.operating_trial(settings)["id"] == TRIAL["id"]


def test_generation_crossing_trial_end_does_not_keep_stale_assignment(app_settings, monkeypatch):
    from toutiao_content_factory.services import weitoutiao_generator as module
    settings = enabled(app_settings)
    end = datetime(2026, 11, 17, tzinfo=NOW.tzinfo)
    candidate = topic("消费者申请退款，核对交付记录")
    policy.rank_revenue_topics([candidate], settings, {}, content_type="weitoutiao", now=end - timedelta(seconds=1))
    assert candidate.revenue_selection["trial_enrolled"] is True

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return end.astimezone(tz) if tz else end

    monkeypatch.setattr(module, "datetime", Clock)
    generator = module.WeiToutiaoStandaloneGenerator(settings, MagicMock())
    generator.openai_service.create_text_completion = MagicMock(return_value="已披露的交付记录与服务约定存在区别。" * 20)
    generator.generate_content(candidate, "hot_comment")
    assert not candidate._generation_cost_context["trial_id"]
    assert "本轮经营结构" not in generator.openai_service.create_text_completion.call_args.kwargs["user_prompt"]
    assignment = generator._opening_experiment(category="consumer_practical", trial_id=TRIAL["id"], body_structure=policy.TRIAL_BODY_STRUCTURE)
    assert "trial_id" not in assignment


@pytest.mark.parametrize("bad", [{"id": "trial"}, {**TRIAL, "started_at": "2026-10-18"},
                                {**TRIAL, "directions": ["entertainment"]}])
def test_invalid_config_cannot_silently_start_an_uncontrolled_trial(app_settings, bad):
    settings = enabled(app_settings)
    object.__setattr__(settings.revenue_optimization, "operating_trial", bad)
    with pytest.raises(ValueError):
        policy.rank_revenue_topics([topic("消费者退款")], settings, {}, content_type="weitoutiao", now=NOW)


def test_trial_and_body_structure_cannot_pool_same_protocol_checkpoints():
    works, entries = [], {}
    for scope, structure in (("trial_a", "facts_v1"), ("trial_b", "facts_v1"), ("trial_a", "facts_v2")):
        for i in range(8):
            gid = f"{scope}-{structure}-{i}"
            exp = {"id": "story_opening_v1", "protocol_version": 4, "selection_protocol": policy.SELECTION_PROTOCOL,
                   "category": "consumer_practical", "variant": "result_first", "trial_id": scope, "body_structure": structure}
            work = {"gid": gid, "title": gid, "type": "weitoutiao", "created_at": (NOW - timedelta(hours=96)).isoformat(), "content_experiment": exp}
            works.append(work)
            entries[gid] = {**work, "checkpoints": {"72h": {"captured_at": (NOW - timedelta(hours=24)).isoformat(),
                "capture_delay_hours": 0, "impressions": 200, "reads": 1, "comments": 0}}}
    result = policy.build_revenue_feedback({"observed_at": NOW.isoformat(), "account": "robot_notes", "works": works}, {"works": entries})
    assert len(result["groups"]) == 3
    assert all(g["samples"] == 8 and g["status"] == "collecting" for g in result["groups"])


def test_candidate_cost_context_preserves_failed_attempts_without_account_leakage(app_settings, logger):
    scope = getattr(costs, "content_cost_scope", None)
    assert callable(scope), "Per-candidate request ownership is missing"
    settings = enabled(app_settings)
    meta = {"account_key": "robot_notes", "content_attempt_id": "a" * 32,
            "trial_id": TRIAL["id"], "direction": "consumer_practical", "topic_title": "消费者退款", "source_url": "https://source.example/a"}
    with scope(meta):
        with pytest.raises(RuntimeError):
            with costs.GenerationCostAttempt(settings, logger, kind="text", model="existing", sdk_retries=0):
                raise RuntimeError("provider unavailable")
        other = replace(settings, account_key="another")
        with costs.GenerationCostAttempt(other, logger, kind="text", model="existing", sdk_retries=0):
            pass
    with costs.GenerationCostAttempt(settings, logger, kind="text", model="existing", sdk_retries=0):
        pass
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (settings.output_dir / "growth_ops/generation_costs/events").glob("*.json")]
    owned = [r for r in rows if r.get("content_attempt_id") == "a" * 32]
    assert len(owned) == 1 and owned[0]["status"] == "failed"
    assert owned[0]["actual_amount"] is None
    assert owned[0]["content_trial_id"] == TRIAL["id"]
    assert all("content_attempt_id" not in r for r in rows if r["account_key"] == "another")


def test_observed_generic_advice_is_not_substantive_reader_analysis():
    text = ("本案贷款75万元，法院未支持调整后罚息。\n\n"
            "银行合同条款复杂，消费者需仔细阅读。本案中，银行未尽到提示义务，消费者可主张公平交易权。\n\n"
            "消费者应主动了解合同细节，避免被动接受不利条款。\n\n"
            "如果合同条款对你不利，是否一定能要求修改？")
    assert generic_padding_reason(text) == "known_generic_padding"


def test_full_real_draft_is_flagged_but_concrete_fact_distinction_remains_usable():
    real = (Path(__file__).parent / "fixtures/operating_trial_baseline_micro.md").read_text(encoding="utf-8")
    assert generic_padding_reason(real) == "known_generic_padding"
    useful = ("该案于2026年公布：银行要求适用调整后利率，法院未支持。争议在于调息条款未达到提示标准。\n\n"
              "法院支持的是原合同项下的本金及相应息费，不支持依另一调息条款追加的部分。"
              "未支持追加请求与免除全部还款责任，是两件不同的事。\n\n"
              "本案的结论取决于具体条款和提示方式，不能仅凭有无加粗推断其他合同的效力。")
    assert generic_padding_reason(useful) == ""


@pytest.mark.parametrize("active", [True, False])
def test_free_cover_trial_never_attempts_paid_api_and_keeps_candidate_ownership(app_settings, logger, tmp_path, monkeypatch, active):
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    from toutiao_content_factory.services.image_generation_service import ImageGenerationService
    from toutiao_content_factory.services import wechat_cloud_cover
    from toutiao_content_factory.services import image_generation_service as module
    instant = NOW if active else datetime(2026, 11, 17, tzinfo=NOW.tzinfo)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant
    monkeypatch.setattr(module, "datetime", Clock)
    settings = enabled(app_settings)
    package = tmp_path / "candidate"
    publish = package / settings.review.publish_dirname
    publish.mkdir(parents=True)
    meta = {"account_key": "robot_notes", "content_attempt_id": "b" * 32, "trial_id": TRIAL["id"], "direction": "consumer_practical"}
    (publish / "publish_meta.json").write_text(json.dumps({"generation_cost_context": meta}), encoding="utf-8")
    service = ImageGenerationService(settings, logger)
    service._generate_api_image = MagicMock(side_effect=AssertionError("Paid API prohibited"))
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "test-token")
    monkeypatch.setattr(service, "_api_backend_requested", lambda: True)
    def free(title, destination, **kwargs):
        with costs.GenerationCostAttempt(settings, logger, kind="free_image", model="existing", sdk_retries=0):
            Image.new("RGB", (640, 400), "white").save(destination)
    monkeypatch.setattr(wechat_cloud_cover, "generate_cloud_cover", free)
    monkeypatch.setattr(service, "_compose_toutiao_illustration", lambda source, dest: Image.open(source).save(dest))
    with ThreadPoolExecutor(max_workers=1) as executor:
        image = executor.submit(service.generate_weitoutiao_image, package, "消费者申请退款，服务是否交付需看已披露的具体记录。另一项约定与已确认的结果仍有区别。").result(timeout=10)
    assert image.is_file()
    if active:
        service._generate_api_image.assert_not_called()
    else:
        service._generate_api_image.assert_called_once()  # existing routing restored at exact end
    report = json.loads((publish / "weitoutiao_image_result.json").read_text(encoding="utf-8"))
    assert report["api_attempted"] is (not active) and report["backend"] == "cloudflare_flux"
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (settings.output_dir / "growth_ops/generation_costs/events").glob("*.json")]
    assert len(rows) == 1 and rows[0]["content_attempt_id"] == "b" * 32
    assert rows[0]["actual_amount"] is None
