import json
import logging
from datetime import datetime, timedelta

import pytest

from toutiao_content_factory.services.revenue_attribution_service import (
    CostRecord, PerformanceSnapshot, RevenueAttributionService, RevenueEvent,
)


@pytest.fixture
def content(tmp_path):
    service = RevenueAttributionService(tmp_path, logging.getLogger('cost-evidence'))
    identity = service.build_content_identity(
        account_key='robot_notes', platform='toutiao', run_dir=tmp_path/'run',
        title='真实标题', topic_title='真实选题',
        published_at=(datetime.now()-timedelta(days=3)).isoformat(),
    )
    service.register_content(identity)
    service.record_snapshot(identity.content_id, PerformanceSnapshot(
        snapshot_label='72h', snapshot_at=datetime.now().isoformat(),
        impression_count=1000, read_count=100,
        platform_revenue=2.0, platform_revenue_status='available',
    ))
    return service, identity.content_id


def test_missing_cost_is_unknown_and_excluded_from_profit_ranking(content):
    service, cid = content
    result = service.compute_unit_economics(cid)
    assert result.net_profit is None
    assert result.rpm is None
    assert result.cost_status == 'unknown'
    assert result.profit_analysis_eligible is False
    assert service.build_unit_economics_ranking(sort_by='net_profit') == []
    assert len(service.build_unit_economics_ranking(sort_by='total_revenue')) == 1


def test_recorded_cost_without_provenance_cannot_prove_profit(content):
    service, cid = content
    service.record_cost(cid, CostRecord(api_cost=0.5))
    result = service.compute_unit_economics(cid)
    assert result.net_profit is None
    assert result.cost_status == 'declared'


def test_automatic_estimate_is_not_an_actual_zero_cost(content, tmp_path):
    service, _ = content
    run = tmp_path/'generated'
    run.mkdir()
    (run/'workflow_result.json').write_text('{"estimated_cost":0}', encoding='utf-8')
    identity = service.register_published_run(
        account_key='robot_notes', platform='toutiao', run_dir=run, title='自动选题',
    )
    result = service.compute_unit_economics(identity.content_id, platform_revenue=2)
    assert result.cost_status == 'estimated'
    assert result.net_profit is None


@pytest.mark.parametrize('amount', [0, 0.5])
def test_actual_complete_cost_retains_evidence_and_enables_content_contribution(content, amount):
    service, cid = content
    service.record_cost(cid, CostRecord(
        api_cost=amount, basis='actual', evidence_ref='provider-export.csv#request-1',
        coverage_complete=True,
    ))
    result = service.compute_unit_economics(cid)
    assert result.net_profit == 2 - amount
    assert result.rpm == 2 - amount
    assert result.profit_analysis_eligible
    assert service.get_cost(cid).evidence_ref == 'provider-export.csv#request-1'


def test_actual_cost_requires_evidence_and_complete_coverage(content):
    service, cid = content
    with pytest.raises(ValueError):
        service.record_cost(cid, CostRecord(basis='actual', coverage_complete=True))
    service.record_cost(cid, CostRecord(basis='actual', evidence_ref='bill.csv', coverage_complete=False))
    assert service.compute_unit_economics(cid).net_profit is None


def test_currency_mismatch_cannot_produce_profit(content):
    service, cid = content
    service.record_cost(cid, CostRecord(
        basis='actual', evidence_ref='bill.csv', coverage_complete=True,
    ))
    service.record_revenue_event(cid, RevenueEvent('deal', 1, currency='USD', event_id='usd-deal'))
    assert service.compute_unit_economics(cid).net_profit is None


def test_profit_report_and_experiment_preserve_missingness(content):
    service, cid = content
    report = service.build_business_report().read_text(encoding='utf-8')
    assert 'NOT_FOUND' in report
    assert '已知口径净利润：2.00' not in report
    experiment = service.build_experiment_report()
    row = experiment['dimensions']['platform']['toutiao']
    assert row['net_profit'] is None
    assert row['rpm'] is None
    assert row['profit_samples'] == 0


def test_legacy_cost_payload_is_readable_but_not_actual(content):
    service, cid = content
    service.cost_ledger_path.write_text(json.dumps({cid: {'api_cost': 0}}), encoding='utf-8')
    assert service.get_cost(cid).api_cost == 0
    assert service.compute_unit_economics(cid).net_profit is None


def test_growth_report_cannot_reuse_legacy_unverified_profit(app_settings):
    from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
    service = GrowthOpsService(app_settings, logging.getLogger('cost-evidence'))
    report = service._summarize_performance([{
        'content_id': 'ci_legacy', 'metrics_latest': {'impression_count': 100},
        'unit_economics': {'revenue_analysis_eligible': True, 'net_profit': 2, 'rpm': 20},
    }])
    assert report['top_by_net_profit'] == []
    assert report['top_by_rpm'] == []


def test_business_report_never_adds_different_currencies(content, tmp_path):
    service, cid = content
    other = service.build_content_identity(account_key='robot_notes', platform='wechat',
        run_dir=tmp_path/'usd', title='美元收入', topic_title='另一个选题', published_at='2026-09-01T00:00:00')
    service.register_content(other)
    service.record_snapshot(other.content_id, PerformanceSnapshot(snapshot_label='72h',
        snapshot_at=datetime.now().isoformat(), platform_revenue=3,
        platform_revenue_status='available', currency='USD'))
    service.record_cost(other.content_id, CostRecord(currency='USD', basis='actual',
        evidence_ref='usd.csv', coverage_complete=True))
    service.record_cost(cid, CostRecord(basis='actual', evidence_ref='cny.csv', coverage_complete=True))
    text = service.build_business_report().read_text(encoding='utf-8')
    assert '5.00 CNY' not in text
    assert 'USD' in text and 'CNY' in text


def test_automatic_registration_cannot_replace_actual_cost_with_estimate(content, tmp_path):
    service, cid = content
    service.record_cost(cid, CostRecord(api_cost=1, basis='actual', evidence_ref='bill.csv', coverage_complete=True))
    service.register_published_run(account_key='robot_notes', platform='toutiao',
        run_dir=tmp_path/'run', estimated_cost=0)
    assert service.get_cost(cid).basis == 'actual'
    assert service.get_cost(cid).api_cost == 1


@pytest.fixture
def two_currency_content(content, tmp_path):
    service, cid = content
    other = service.build_content_identity(account_key='robot_notes', platform='toutiao',
        run_dir=tmp_path/'usd', title='美元收入', topic_title='另一个选题', published_at='2026-09-01T00:00:00')
    service.register_content(other)
    service.record_snapshot(other.content_id, PerformanceSnapshot(snapshot_label='72h',
        snapshot_at=datetime.now().isoformat(), impression_count=1000,
        platform_revenue=3, platform_revenue_status='available',currency='USD'))
    for item,currency in ((cid,'CNY'),(other.content_id,'USD')):
        service.record_cost(item,CostRecord(currency=currency,basis='actual',evidence_ref='bill.csv',coverage_complete=True))
    return service


def test_experiment_withholds_cross_currency_monetary_totals(two_currency_content):
    service=two_currency_content
    row=service.build_experiment_report()['dimensions']['platform']['toutiao']
    assert row['net_profit'] is None and row['rpm'] is None
    assert row['revenue_amount'] is None and row['total_cost'] is None


def test_profit_rankings_preserve_currency_and_do_not_compare_between_groups(two_currency_content, app_settings):
    from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
    service=two_currency_content
    ranking=service.build_unit_economics_ranking()
    assert [item['currency'] for item in ranking]==['CNY','USD']
    text='\n'.join(service._build_ranking_lines(ranking,metric='net_profit'))
    assert '3.00 USD' in text and '3.00 CNY' not in text
    growth=GrowthOpsService(app_settings,logging.getLogger('currency'))
    rows=[{'content_id':entry['content_id'],'title':entry['title'],
           'metrics_latest':{'impression_count':1000},'unit_economics':entry} for entry in ranking]
    summary=growth._summarize_performance(rows)
    assert [item['currency'] for item in summary['top_by_net_profit']]==['CNY','USD']
    text=growth._build_performance_markdown({'summary':summary})
    assert '3.00 USD' in text
