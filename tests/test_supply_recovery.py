"""Offline regressions for the supply failures observed on 2026-10-20."""
import re
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.revenue_feedback import trial_direction
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


@pytest.mark.parametrize('title,direction', [
    ('AI 引爆 BAT「薪酬倒挂」潮，老员工：人麻了', 'technology_everyday'),
    ('纽娲机器人杨睿刚：无人配送跑了十年，机器人为何还到不了家门？', 'technology_everyday'),
    ('博通 Wi‑Fi 漏洞曝光：无需密码，攻击者也能拿下你的路由器', 'technology_everyday'),
    ('万亿级机器人市场，可否成为美的第二增长曲线？', ''),
    ('平台做天使投资：速卖通把品牌争夺战，打到了品牌诞生前', ''),
    ('新能源公司利润上涨，资本市场关注订单', ''),
    ('员工薪酬变化与住房价格', ''),
    ('网购预付款退款争议', 'consumer_practical'),
])
def test_existing_trial_recognizes_concrete_work_and_home_technology(title, direction):
    assert trial_direction(TopicCandidate(title, 'https://source.test/article', 'rss', 'rss')) == direction


def test_daily_priority_uses_fresh_collection_without_extra_collection(app_settings, logger):
    app = ContentFactoryAppService(app_settings, logger)
    events = []
    app._cleanup_stale_runs = MagicMock()
    app._heartbeat_daily_run_lock = MagicMock()
    app.collect_topics = MagicMock(side_effect=lambda: events.append('collect') or {'daily_topics': 'fresh.json'})
    app._publish_priority_weitoutiao = MagicMock(side_effect=lambda **kw: events.append('priority') or {})
    app.platform_health_service.ensure_publish_ready = MagicMock(return_value={'blocking_platforms': []})
    app._build_daily_candidate_specs = MagicMock(return_value=[{'rank': 1, 'title': '测试', 'url': 'https://source.test/a'}])
    app.auto_run = MagicMock(side_effect=ValueError('Selected topic was rejected by the local bad-word guard. Matched bad words: 押注 (bad_words_blocked=True).'))
    app._daily_run_impl(dry_run=True)
    assert events == ['collect', 'priority']


def test_verified_feed_reflows_existing_sentences_without_changing_claims(app_settings, logger):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logger)
    topic = TopicCandidate('配送机器人如何进家门', 'https://source.test/a', 'rss', 'rss',
                           snippet='据报道，机器人配送测试持续进行。' * 80)
    topic._verified_source_material = True
    lead = '配送机器人仍在小区外等待交接。报道确认上楼与道路行驶属于不同测试阶段。'
    body = ('报道描述的是路面运输的进展，楼内交接仍需结合具体场景核对。'
            '道路测试与家庭交付的对象不同，不能直接把一个阶段的结果当成另一个阶段已经完成。'
            '读者能从现有信息确认运输环节的变化，不能据此推断自己的住址已经覆盖。'
            '报道未给出覆盖范围时，应当保留这个判断边界。')
    raw = lead + body + '\n\n#机器人 #配送'
    generator.openai_service.create_text_completion = MagicMock(side_effect=[raw, raw])
    result = generator.generate_content(topic, 'story_comment')
    assert generator._feed_opening_reason(result) == ''
    assert re.sub(r'\s+', '', result) == re.sub(r'\s+', '', raw)
    assert generator.openai_service.create_text_completion.call_count == 2


def test_reflow_does_not_approve_an_unsplittable_sentence_or_added_number(app_settings, logger):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logger)
    topic = TopicCandidate('配送机器人如何进家门', 'https://source.test/a', 'rss', 'rss',
                           snippet='据报道，机器人测试仍在进行。' * 80)
    topic._verified_source_material = True
    raw = '配送机器人仍等待交接。道路与上楼是不同阶段。\n\n' + '来源未说明的长句' * 22 + '。\n\n#配送'
    generator.openai_service.create_text_completion = MagicMock(side_effect=[raw, raw])
    result = generator.generate_content(topic, 'story_comment')
    assert generator._feed_opening_reason(result) == 'story_paragraph_length_guard'
    assert generator._model_story_fallback_reason(topic, 'story_comment', result + '已完成99万次。') == 'story_number_evidence_guard'


def test_short_rejection_retains_model_length_and_original_guard(app_settings, logger):
    object.__setattr__(app_settings.publishing, 'weitoutiao_enabled', True)
    object.__setattr__(app_settings.publishing, 'weitoutiao_personas', ['story_comment'])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logger)
    topic = TopicCandidate('医保政策调整', 'https://source.test/policy', 'rss', 'rss')
    generator._iter_eligible_topics = MagicMock(return_value=[topic])
    draft = '未经核实的政策解释。' * 25
    generator.generate_content = MagicMock(return_value=draft)
    generator.publish_service.publish_weitoutiao = MagicMock()
    result = generator.publish_standalone(count=1)
    skipped = result['skipped'][0]
    assert skipped['reason'] == 'content_too_short'
    assert skipped['generation_fallback_reason'] == 'policy_story_evidence_guard'
    assert skipped['model_content_char_count'] == generator._content_char_count(draft)
    assert skipped['content_char_count'] == 0
    generator.publish_service.publish_weitoutiao.assert_not_called()
