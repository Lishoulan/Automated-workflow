import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from toutiao_content_factory.services.billing_reconciliation import reconcile_billing_csv
from toutiao_content_factory.services.generation_cost_service import BEIJING, build_cost_summary


@pytest.fixture
def bill(tmp_path):
    events = tmp_path/'growth_ops/generation_costs/events'
    events.mkdir(parents=True)
    now = datetime.now(BEIJING)-timedelta(hours=1)
    row = dict(schema=1, event_id='a'*32, account_key='robot_notes',
               provider_request_id='request-1', started_at=now.isoformat(),
               kind='text', status='succeeded', sdk_retries=0,
               actual_amount=None, actual_currency=None, billing_status='unverified')
    path = events/(row['event_id']+'.json')
    path.write_text(json.dumps(row), encoding='utf-8')
    csv_path = tmp_path/'bill.csv'
    def write(rows=None):
        rows = rows or [dict(account_key='robot_notes', provider_request_id='request-1',
                            amount='0', currency='CNY', charged_at=now.isoformat(), status='charged')]
        with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return csv_path
    write()
    return tmp_path, path, csv_path, row, write


def test_preview_is_read_only_and_apply_is_idempotent(bill):
    root, path, csv_path, _, _ = bill
    before = path.read_bytes()
    result = reconcile_billing_csv(root, csv_path, account_key='robot_notes')
    assert result['matched'] == 1
    assert result['applied'] == 0
    assert path.read_bytes() == before
    assert reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)['applied'] == 1
    after = path.read_bytes()
    assert reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)['applied'] == 0
    assert path.read_bytes() == after
    summary = build_cost_summary(root)
    assert summary['actual_total'] == 0
    assert summary['imported_cost_calls'] == 1
    assert summary['net_profit'] is None


@pytest.mark.parametrize('change', [
    {'account_key': 'other'}, {'sdk_retries': 1}, {'sdk_retries': False}, {'status': 'started'},
])
def test_unmatched_or_incomplete_receipt_is_rejected_without_mutation(bill, change):
    root, path, csv_path, row, _ = bill
    row.update(change)
    path.write_text(json.dumps(row), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    assert path.read_bytes() == before


def test_duplicate_request_receipts_cannot_share_one_charge(bill):
    root, path, csv_path, row, _ = bill
    row['event_id'] = 'b'*32
    path.with_name(row['event_id']+'.json').write_text(json.dumps(row), encoding='utf-8')
    with pytest.raises(ValueError, match='ambiguous'):
        reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '-1', 'true', ''])
def test_invalid_money_is_not_zero(bill, amount):
    root, _, csv_path, row, write = bill
    write([dict(account_key='robot_notes', provider_request_id='request-1', amount=amount,
                currency='CNY', charged_at=row['started_at'], status='charged')])
    with pytest.raises(ValueError):
        reconcile_billing_csv(root, csv_path, account_key='robot_notes')


def test_conflicting_statement_is_rejected_and_prior_evidence_preserved(bill):
    root, path, csv_path, row, write = bill
    reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    before = path.read_bytes()
    write([dict(account_key='robot_notes', provider_request_id='request-1', amount='1',
                currency='CNY', charged_at=row['started_at'], status='charged')])
    with pytest.raises(ValueError, match='conflict'):
        reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    assert path.read_bytes() == before


def test_file_preflight_prevents_partial_import(bill):
    root, path, csv_path, row, write = bill
    base = dict(account_key='robot_notes', provider_request_id='request-1', amount='1',
                currency='CNY', charged_at=row['started_at'], status='charged')
    write([base, {**base, 'provider_request_id':'missing-request'}])
    before = path.read_bytes()
    with pytest.raises(ValueError):
        reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    assert path.read_bytes() == before


def test_duplicate_provider_charge_is_not_double_counted_in_summary(bill):
    root, path, csv_path, row, _ = bill
    reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    duplicate = json.loads(path.read_text(encoding='utf-8'))
    duplicate['event_id'] = 'b'*32
    path.with_name(duplicate['event_id']+'.json').write_text(json.dumps(duplicate), encoding='utf-8')
    summary = build_cost_summary(root)
    assert summary['actual_total'] is None
    assert summary['ambiguous_request_count'] == 1


def test_interrupted_import_can_be_retried_without_double_charge(bill, monkeypatch):
    from toutiao_content_factory.services import billing_reconciliation as module
    root, path, csv_path, row, write = bill
    row.update(event_id='b'*32, provider_request_id='request-2')
    second = path.with_name(row['event_id']+'.json')
    second.write_text(json.dumps(row), encoding='utf-8')
    base = dict(account_key='robot_notes', provider_request_id='request-1', amount='1',
                currency='CNY', charged_at=row['started_at'], status='charged')
    write([base, {**base, 'provider_request_id':'request-2'}])
    real_replace = module.os.replace
    def interrupted(source, destination):
        if Path(destination) == second:
            raise OSError('simulated interruption')
        return real_replace(source, destination)
    with monkeypatch.context() as context:
        context.setattr(module.os, 'replace', interrupted)
        with pytest.raises(OSError):
            reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    result = reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    assert result['applied'] == 1 and result['unchanged'] == 1
    assert build_cost_summary(root)['actual_total'] == 2


def test_unsupported_decimal_precision_is_rejected_before_any_write(bill):
    root, path, csv_path, row, write = bill
    before = path.read_bytes()
    write([dict(account_key='robot_notes', provider_request_id='request-1',
                amount='0.10000000000000001', currency='CNY', charged_at=row['started_at'], status='charged')])
    with pytest.raises(ValueError, match='precision'):
        reconcile_billing_csv(root, csv_path, account_key='robot_notes', apply=True)
    assert path.read_bytes() == before
