"""Build a read-only cross-platform view from existing exported artifacts."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from toutiao_content_factory.services.operating_evidence import build_operating_evidence, render_operating_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toutiao-output', type=Path, required=True)
    parser.add_argument('--toutiao-account', required=True)
    parser.add_argument('--wechat-state', type=Path, required=True)
    parser.add_argument('--wechat-account', required=True)
    parser.add_argument('--wechat-account-key', help='Receipt account key, distinct from the display name; missing binding keeps costs unknown')
    parser.add_argument('--wechat-output', type=Path)
    parser.add_argument('--v2-database', type=Path)
    parser.add_argument('--as-of', type=datetime.fromisoformat)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    roots = [args.toutiao_output, args.wechat_state.parent]
    if args.wechat_output:
        roots.append(args.wechat_output)
    if args.v2_database:
        roots.append(args.v2_database.parent)
    if any(destination == root.resolve() or root.resolve() in destination.parents for root in roots):
        parser.error('Output must be outside source directories')
    try:
        report = build_operating_evidence(toutiao_output=args.toutiao_output,
            toutiao_account=args.toutiao_account, wechat_state=args.wechat_state,
            wechat_account=args.wechat_account, wechat_output=args.wechat_output,
            wechat_account_key=args.wechat_account_key,
            v2_database=args.v2_database, as_of=args.as_of)
        destination.mkdir(parents=True, exist_ok=True)
        (destination/'operating_evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        (destination/'operating_evidence.md').write_text(render_operating_evidence(report), encoding='utf-8')
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f'Evidence build refused: {exc}\n')
    print(json.dumps({'output':str(destination), 'net_profit':report['net_profit'],
                      'platforms':{key:row['evidence_status'] for key,row in report['platforms'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
