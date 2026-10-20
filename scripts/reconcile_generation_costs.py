"""Offline billing reconciliation. Default preview never changes receipts."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from toutiao_content_factory.services.billing_reconciliation import reconcile_billing_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot-dir', type=Path, required=True, help='Quiescent copy of account output; never a live producer directory')
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--apply', action='store_true', help='Update the specified offline receipt copy after complete preflight')
    args = parser.parse_args()
    try:
        report = reconcile_billing_csv(args.snapshot_dir, args.csv, account_key=args.account, apply=args.apply)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Reconciliation refused: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
