import copy
import json
import sqlite3
from datetime import datetime

import pytest

from toutiao_content_factory.services.operating_evidence import build_operating_evidence, content_key, read_v2_coverage
from toutiao_content_factory.services.generation_cost_service import BEIJING


@pytest.fixture
def sources(tmp_path):
    tt = tmp_path/'toutiao'
    tt.mkdir()
    snapshot = {'account':'robot_notes', 'observed_at':'2026-10-19T21:30:08+08:00',
                'works':[{'gid':'123', 'created_at':'2026-10-18T12:00:00+08:00',
                          'type':'weitoutiao', 'title':'真实标题', 'impressions':100, 'reads':None}]}
    (tt/'growth_snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
    state = {'schema':1, 'account':'小椰科技', 'updated_at':'2026-10-17T23:00:00+08:00',
             'account_snapshots':[{'observed_at':'2026-10-17T23:00:00+08:00','followers':67}],
             'coverage':'visible articles only', 'articles':{
                 'https://mp.weixin.qq.com/s/abc':{'url':'https://mp.weixin.qq.com/s/abc','title':'公众号正文',
                    'published_at':'2026-10-15T00:00:00+08:00','snapshots':[
                        {'observed_at':'2026-10-16T01:00:00+08:00','readers':4},
                        {'observed_at':'2026-10-17T23:00:00+08:00','readers':7}]}}}
    wx = tmp_path/'wechat.json'
    wx.write_text(json.dumps(state), encoding='utf-8')
    return tt, wx, state, snapshot


def build(sources):
    tt, wx, _, _ = sources
    return build_operating_evidence(toutiao_output=tt, toutiao_account='robot_notes',
        wechat_state=wx, wechat_account='小椰科技',
        as_of=datetime(2026,10,20,tzinfo=BEIJING))


def test_platform_scoped_identity_is_stable_across_paths():
    assert content_key('toutiao','a','1') == content_key('toutiao','a','1')
    assert content_key('toutiao','a','1') != content_key('wechat','a','1')
    assert content_key('toutiao','a','1') != content_key('toutiao','b','1')


def test_unknown_metrics_and_stale_state_remain_explicit(sources):
    report = build(sources)
    tt, wx = report['platforms']['toutiao'], report['platforms']['wechat']
    assert tt['records'][0]['metrics']['reads'] is None
    assert tt['money']['net_profit'] is None
    assert wx['freshness']['status'] == 'STALE'
    assert wx['evidence_status'] == 'PARTIAL'
    assert wx['records'][0]['metrics']['readers'] == 7
    assert wx['records'][0]['metrics']['impressions'] is None
    assert wx['native_report']['ad_activation'] == 'unverified'
    assert report['net_profit'] is None


def test_duplicate_snapshots_are_not_added_to_lifetime_metrics(sources):
    report = build(sources)
    assert report['platforms']['wechat']['records'][0]['metrics']['readers'] == 7
    assert len(report['platforms']['wechat']['records']) == 1


def test_wrong_account_and_conflicting_native_ids_are_not_silently_merged(sources):
    tt, _, _, snapshot = sources
    snapshot['account'] = 'different'
    (tt/'growth_snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
    with pytest.raises(ValueError, match='account'):
        build(sources)
    snapshot['account'] = 'robot_notes'
    snapshot['works'].append({**snapshot['works'][0], 'reads':999})
    (tt/'growth_snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
    report = build(sources)
    assert report['platforms']['toutiao']['records'] == []
    assert report['platforms']['toutiao']['data_quality_errors']


def test_missing_sources_do_not_become_an_empty_verified_business(tmp_path):
    report = build_operating_evidence(toutiao_output=tmp_path/'missing', toutiao_account='a',
        wechat_state=tmp_path/'absent.json', wechat_account='wx', as_of=datetime.now(BEIJING))
    assert all(row['evidence_status'] == 'NOT_FOUND' for row in report['platforms'].values())
    assert report['net_profit'] is None
    assert not (tmp_path/'missing').exists()


def test_build_is_read_only(sources):
    tt, wx, _, _ = sources
    before = {p:p.read_bytes() for p in [*tt.rglob('*'),wx] if p.is_file()}
    build(sources)
    assert {p:p.read_bytes() for p in [*tt.rglob('*'),wx] if p.is_file()} == before


def test_v2_is_opened_without_migrations_or_writes(tmp_path):
    database = tmp_path/'v2.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA user_version=8')
        db.execute('CREATE TABLE content(content_id TEXT, platform_item_id TEXT)')
        db.execute('CREATE TABLE performance(content_id TEXT)')
        db.execute("INSERT INTO content VALUES ('legacy-1', '123')")
    before = database.read_bytes()
    result = read_v2_coverage(database)
    assert result['schema_version'] == 8
    assert result['content_count'] == 1
    assert result['platform_aliases']['123'] == ['legacy-1']
    assert database.read_bytes() == before
    assert read_v2_coverage(tmp_path/'not-found.sqlite3')['status'] == 'NOT_FOUND'
    assert not (tmp_path/'not-found.sqlite3').exists()


def test_unscoped_v2_database_never_establishes_a_content_alias(sources, tmp_path):
    database = tmp_path/'foreign.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE content(content_id TEXT, platform_item_id TEXT)')
        db.execute('CREATE TABLE performance(content_id TEXT)')
        db.execute("INSERT INTO content VALUES ('unrelated-legacy-id','123')")
    tt, wx, _, _ = sources
    report = build_operating_evidence(toutiao_output=tt, toutiao_account='robot_notes',
        wechat_state=wx, wechat_account='小椰科技', v2_database=database,
        as_of=datetime(2026,10,20,tzinfo=BEIJING))
    assert report['platforms']['toutiao']['records'][0].get('source_content_ids', []) == []


def test_account_binding_filters_outer_and_nested_platform_costs(sources, tmp_path):
    tt, wx, _, _ = sources
    for root in (tt, tmp_path/'wx-output'):
        events=root/'growth_ops/generation_costs/events'
        events.mkdir(parents=True)
        for number, account in enumerate(('robot_notes', 'default', 'foreign-account'),1):
            row = dict(schema=1, event_id=str(number)*32, account_key=account,
                started_at='2026-10-19T20:00:00+08:00', kind='text', status='succeeded',
                sdk_retries=0, provider_request_id='request-'+str(number),
                billing_status='provider_confirmed', billing_evidence='matched_response_receipt',
                actual_amount=number, actual_currency='CNY')
            (events/(row['event_id']+'.json')).write_text(json.dumps(row),encoding='utf-8')
    account_path=tt/'growth_ops/performance/account_snapshot.json'
    account_path.parent.mkdir(parents=True)
    account_path.write_text(json.dumps({'latest':{'account_key':'robot_notes',
        'generated_at':'2026-10-19T21:00:00+08:00'}}), encoding='utf-8')
    report=build_operating_evidence(toutiao_output=tt,toutiao_account='robot_notes',
        wechat_state=wx,wechat_account='小椰科技',wechat_output=tmp_path/'wx-output',
        wechat_account_key='default',as_of=datetime(2026,10,20,tzinfo=BEIJING))
    assert report['platforms']['toutiao']['money']['generation_costs']['actual_total']==1
    assert report['platforms']['toutiao']['native_report']['generation_costs']['actual_total']==1
    assert report['platforms']['toutiao']['native_report']['cost_windows']['7']['actual_total']==1
    assert report['platforms']['wechat']['money']['generation_costs']['actual_total']==2
    # Without a key, display account names must not implicitly claim receipt ownership.
    report=build_operating_evidence(toutiao_output=tt,toutiao_account='robot_notes',
        wechat_state=wx,wechat_account='小椰科技',wechat_output=tmp_path/'wx-output',
        as_of=datetime(2026,10,20,tzinfo=BEIJING))
    assert report['platforms']['wechat']['money']['generation_costs']['actual_total'] is None
