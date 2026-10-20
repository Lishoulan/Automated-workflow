"""Reconcile operator-supplied billing exports against quiescent receipt copies.

No account API is called. Preview is the default. Import validates linkage and
amounts; the operator remains responsible for the export's provenance. A crash
between atomic receipt writes is recoverable by repeating the same import.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            return stamp
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError('Billing timestamp requires a timezone')


def reconcile_billing_csv(snapshot_dir: Path, csv_path: Path, *, account_key: str, apply=False):
    """Preflight the whole export, then update only exact, unique receipt IDs."""
    raw = Path(csv_path).read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    required = {'account_key', 'provider_request_id', 'amount', 'currency', 'charged_at', 'status'}
    if not reader.fieldnames or set(reader.fieldnames) != required or len(reader.fieldnames) != len(required):
        raise ValueError('Expected exact billing CSV columns: ' + ','.join(sorted(required)))
    charges = {}
    for number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Malformed billing CSV row')
        row = {key: value.strip() for key, value in row.items()}
        if row['account_key'] != account_key or not account_key:
            raise ValueError('Billing account mismatch')
        request = row['provider_request_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request) or request.startswith(('sk-', 'cfut_')):
            raise ValueError('Invalid provider request ID')
        try:
            amount = Decimal(row['amount'])
            if not amount.is_finite() or amount < 0 or amount > Decimal('1e12'):
                raise ValueError('Invalid billing amount')
            if Decimal(str(float(amount))) != amount:
                raise ValueError('Unsupported billing amount precision for existing receipt format')
        except InvalidOperation as exc:
            raise ValueError('Invalid billing amount') from exc
        if row['currency'] not in {'CNY', 'USD'} or row['status'] != 'charged':
            raise ValueError('Explicit charged status and supported currency required')
        charged = _stamp(row['charged_at'])
        normalized = (amount, row['currency'], charged)
        if request in charges and charges[request][0] != normalized:
            raise ValueError('Conflicting billing rows for one request')
        charges.setdefault(request, (normalized, number))
    if not charges:
        raise ValueError('Empty billing export')

    receipts = {}
    for path in sorted((Path(snapshot_dir)/'growth_ops/generation_costs/events').glob('*.json')):
        before = path.read_bytes()
        try:
            row = json.loads(before)
            if not isinstance(row, dict):
                raise ValueError('Invalid receipt')
            if row.get('account_key') != account_key:
                continue
            if row.get('schema') != 1 or row.get('event_id') != path.stem:
                raise ValueError('Invalid receipt identity')
            request = row.get('provider_request_id')
            if request in charges:
                receipts.setdefault(request, []).append((path, before, row))
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid receipt; refusing partial reconciliation') from exc
    updates, unchanged = [], 0
    for request, ((amount, currency, charged), number) in charges.items():
        matches = receipts.get(request, [])
        if len(matches) != 1:
            raise ValueError('unmatched request' if not matches else 'ambiguous request receipts')
        path, before, row = matches[0]
        if (type(row.get('sdk_retries')) is not int or row['sdk_retries'] != 0
                or row.get('status') not in {'succeeded', 'failed'}):
            raise ValueError('Incomplete invocation or SDK retries require manual reconciliation')
        started = _stamp(row.get('started_at'))
        if not started-timedelta(minutes=5) <= charged <= started+timedelta(days=3):
            raise ValueError('Charge timestamp does not match invocation')
        if row.get('actual_amount') is not None or row.get('billing_status') == 'provider_confirmed':
            if (type(row.get('actual_amount')) not in {int, float}
                    or Decimal(str(row['actual_amount'])) != amount or row.get('actual_currency') != currency):
                raise ValueError('Billing conflict with existing evidence')
            # Preserve the original source, including direct response evidence.
            if row.get('billing_status') == 'provider_confirmed':
                unchanged += 1
                continue
        row.update(actual_amount=float(amount), actual_currency=currency,
                   billing_status='provider_confirmed', billing_evidence='matched_provider_export',
                   billing_import={'source_sha256': source_hash, 'source_name': Path(csv_path).name,
                                   'row': number, 'charged_at': charged.isoformat(),
                                   'account_key': account_key, 'provider_request_id': request})
        updates.append((path, before, row))
    if apply:
        # These must be offline copies. Refuse changed files rather than overwrite
        # another writer's evidence. No claim of a transaction across live producers.
        if any(path.read_bytes() != before for path, before, _ in updates):
            raise ValueError('Receipt changed during reconciliation')
        for path, before, row in updates:
            if path.read_bytes() != before:
                raise ValueError('Receipt changed during reconciliation; retry on an offline copy')
            descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                    json.dump(row, handle, ensure_ascii=False, indent=2, allow_nan=False)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return {'mode': 'apply' if apply else 'preview', 'matched': len(charges),
            'would_update': len(updates), 'applied': len(updates) if apply else 0,
            'unchanged': unchanged, 'source_sha256': source_hash,
            'source_status': 'operator_supplied_export', 'net_profit': None}
