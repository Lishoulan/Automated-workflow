"""Read-only projections over existing operating artifacts; no new ledger store."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

from toutiao_content_factory.services.generation_cost_service import BEIJING, build_cost_summary
from toutiao_content_factory.services.revenue_experiment_service import RevenueExperimentService
from toutiao_content_factory.services.wechat_growth_service import article_url, build_growth_report


def content_key(platform, account, native_id):
    """Projection key; existing persisted content IDs remain exact-ID aliases."""
    if not all(isinstance(value, str) and value for value in (platform, account, native_id)):
        raise ValueError('Platform, account and native identity are required')
    payload = json.dumps([platform, account, native_id], ensure_ascii=False, separators=(',', ':'))
    return 'content_' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]


def _read(path, sources):
    path = Path(path)
    if not path.is_file():
        sources.append({'path': str(path), 'status': 'NOT_FOUND'})
        return {}
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected an object in ' + path.name)
    sources.append({'path': str(path), 'status': 'VERIFIED', 'sha256': hashlib.sha256(raw).hexdigest()})
    return value


def _freshness(stamp, as_of):
    try:
        observed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError('Observation timezone missing')
        age = (as_of-observed).total_seconds()/3600
        return {'observed_at': stamp, 'age_hours': round(age, 2),
                'status': 'FRESH' if 0 <= age <= 24 else 'STALE' if age > 24 else 'FUTURE'}
    except (ValueError, TypeError, AttributeError):
        return {'observed_at': stamp, 'age_hours': None, 'status': 'NOT_FOUND'}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def read_v2_coverage(database):
    """Reuse the existing V2 schema through SQLite's read-only URI, never migrate."""
    path = Path(database).resolve()
    if not path.is_file():
        return {'status': 'NOT_FOUND', 'path': str(path)}
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            aliases = {}
            for content_id, native in db.execute('SELECT content_id, platform_item_id FROM content'):
                if native:
                    aliases.setdefault(str(native), []).append(content_id)
            return {'status': 'PARTIAL', 'path': str(path), 'mode': 'read_only_no_migrations',
                    'schema_version': db.execute('PRAGMA user_version').fetchone()[0],
                    'content_count': db.execute('SELECT COUNT(*) FROM content').fetchone()[0],
                    'snapshot_count': db.execute('SELECT COUNT(*) FROM performance').fetchone()[0],
                    'platform_aliases': aliases,
                    'note': 'Historical V2 coverage only; account scope is supplied by the operator, not embedded in this schema.'}
    except sqlite3.Error as exc:
        return {'status': 'PARTIAL', 'path': str(path), 'error': type(exc).__name__}


def build_operating_evidence(*, toutiao_output, toutiao_account, wechat_state,
                             wechat_account, as_of=None, wechat_output=None, v2_database=None,
                             wechat_account_key=None):
    as_of = as_of or datetime.now(BEIJING)
    if as_of.tzinfo is None:
        raise ValueError('as_of requires timezone')
    root = Path(toutiao_output)
    sources = []
    snapshot = _read(root/'growth_snapshot.json', sources)
    if snapshot and snapshot.get('account') != toutiao_account:
        raise ValueError('Toutiao account mismatch')
    v2 = read_v2_coverage(v2_database) if v2_database else {'status': 'NOT_FOUND'}
    work_rows, conflicts, errors = {}, set(), []
    for row in snapshot.get('works', []):
        native = str(row.get('gid', '')) if isinstance(row, dict) else ''
        if not native.isdigit() or int(native) <= 0:
            errors.append('missing_or_invalid_platform_item_id')
            continue
        if native in work_rows and work_rows[native] != row:
            conflicts.add(native)
        work_rows.setdefault(native, row)
    errors.extend('identity_conflict:'+value for value in sorted(conflicts))
    records = []
    for native, row in sorted(work_rows.items()):
        if native in conflicts:
            continue
        metrics = {key: _count(row.get(key)) for key in ('impressions', 'reads', 'comments')}
        records.append({'content_id': content_key('toutiao', toutiao_account, native),
                        'platform_item_id': native, 'source_content_ids': [],
                        'account': toutiao_account, 'format': row.get('type'), 'title': row.get('title'),
                        'published_at': row.get('created_at'), 'observed_at': snapshot.get('observed_at'),
                        'metrics': metrics, 'scope': 'lifetime_work_counters_at_observation',
                        'revenue': {key: row.get(key) for key in ('platform_revenue', 'platform_revenue_status',
                            'revenue_scope', 'revenue_as_of_date', 'revenue_gid', 'revenue_source')}})
    account_data = _read(root/'growth_ops/performance/account_snapshot.json', sources)
    latest_account = dict(account_data.get('latest') or {})
    if latest_account and latest_account.get('account_key') != toutiao_account:
        raise ValueError('Income account mismatch')
    native_report = {}
    if latest_account:
        latest_account['settled_daily_history'] = account_data.get('settled_daily_history', [])
        service = RevenueExperimentService(root, logging.getLogger(__name__))
        native_report = service.build_dashboard(
            account_snapshot=latest_account, growth_snapshot=snapshot,
            growth_feedback=_read(root/'growth_feedback.json', sources),
            performance=_read(root/'performance_metrics.json', sources),
            readiness=service.build_readiness(latest_account), cost_account_key=toutiao_account)
    freshness = _freshness(snapshot.get('observed_at'), as_of)
    tt = {'account': toutiao_account, 'records': records, 'freshness': freshness,
          'evidence_status': 'NOT_FOUND' if not snapshot else 'PARTIAL' if errors or freshness['status'] != 'FRESH' else 'VERIFIED',
          'data_quality_errors': errors, 'native_report': native_report,
          'money': {'income_status': 'PARTIAL' if latest_account else 'NOT_FOUND',
                    'net_profit': None, 'generation_costs': build_cost_summary(root, now=as_of, account_key=toutiao_account)},
          'coverage': 'Available published-work list, not complete historical activity; formats and cohort ages stay separate.'}

    state = _read(wechat_state, sources)
    if state and state.get('account') != wechat_account:
        raise ValueError('WeChat account mismatch')
    wx_report = build_growth_report(state) if state else {}
    wx_records = []
    seen = set()
    for row in wx_report.get('articles', []):
        native = article_url(row.get('url'))
        if not native or native in seen:
            raise ValueError('Missing or ambiguous WeChat native identity')
        seen.add(native)
        wx_records.append({'content_id': content_key('wechat', wechat_account_key or wechat_account, native),
                           'platform_item_id': native, 'account': wechat_account, 'format': 'article',
                           'title': row.get('title'), 'published_at': row.get('published_at'),
                           'observed_at': row['latest'].get('observed_at'),
                           'metrics': {'impressions': None, **{key:_count(row['latest'].get(key))
                                       for key in ('readers','likes','shares','recommends')}},
                           'windows': row['windows'], 'scope': 'unique_readers_per_article_not_account_unique_people'})
    wx_freshness = _freshness(wx_report.get('observed_at'), as_of)
    wx = {'account': wechat_account, 'account_key': wechat_account_key, 'records': wx_records, 'freshness': wx_freshness,
          'evidence_status': 'NOT_FOUND' if not state else 'PARTIAL',
          'native_report': wx_report, 'coverage': wx_report.get('coverage'),
          'money': {'income_status':'PARTIAL' if wx_report.get('monetization',{}).get('income') else 'NOT_FOUND',
                    'net_profit':None, 'generation_costs': build_cost_summary(wechat_output, now=as_of, account_key=wechat_account_key)
                        if wechat_output and wechat_account_key else {'status':'NOT_FOUND','actual_total':None,
                                                                    'reason':'output_or_receipt_account_binding_missing'}}}
    return {'schema':1, 'as_of':as_of.isoformat(), 'sources':sources, 'v2':v2,
            'platforms':{'toutiao':tt, 'wechat':wx}, 'net_profit':None,
            'decision':'DO_NOT_EXPAND_SPENDING_WITHOUT_COMPLETE_ALIGNED_INCOME_AND_COST',
            'note':'Projection only. Source ledgers, experiments, budgets and publication permissions are unchanged. Account totals, settled daily income and cumulative work revenue are distinct scopes and never added together.'}


def render_operating_evidence(report):
    lines = ['# 两平台经营证据', '', '核验时点：'+report['as_of'], '',
             '| 平台 | 证据 | 观测时间 | 新鲜度 | 已取得作品 | 净收益 |',
             '|---|---|---|---|---:|---|']
    for key, row in report['platforms'].items():
        lines.append(f"| {key} | {row['evidence_status']} | {row['freshness']['observed_at']} | {row['freshness']['status']} | {len(row['records'])} | NOT_FOUND |")
    lines.extend(['', '当前不能验证经营净盈利。已取得作品数量是可见列表覆盖，不是全量历史。',
                  '头条展现/阅读、公众号阅读人数，以及各平台的累计/日入账/单篇累计收益保持独立口径。',
                  '费用缺失不填0，导入账单不证明全部成本覆盖，草稿与只读采集不算新增发表。',
                  '字段、来源SHA-256、费用覆盖、原生经营报告和历史V2覆盖见配套JSON。', ''])
    return '\n'.join(lines)
