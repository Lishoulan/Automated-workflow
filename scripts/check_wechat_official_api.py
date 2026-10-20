"""Read-only official API/IP probe, or query an existing persisted publish job."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.services.wechat_official_publish import query_job


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', default='default')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--expected-app-id-sha256', help='Hash observed on the intended account settings page')
    parser.add_argument('--job', type=Path, help='Query only; never creates a draft or submits a job')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/automation/wechat_official_api_check.json')
    args = parser.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    settings = load_settings(ROOT, account_key=args.account)
    digest = hashlib.sha256(settings.wechat.app_id.encode()).hexdigest() if settings.wechat.app_id else None
    binding = ('matched_backend_app_id' if args.expected_app_id_sha256 and digest == args.expected_app_id_sha256
               else 'mismatch' if args.expected_app_id_sha256 else 'not_independently_verified')
    if binding == 'mismatch' or (os.environ.get('WECHAT_EXECUTION_HOST') == 'cloud'
                                 and binding != 'matched_backend_app_id'):
        report = {'status': 'account_binding_mismatch' if binding == 'mismatch' else 'account_binding_unverified',
                  'publication_attempted': False, 'draft_saving_attempted': False,
                  'formal_submit_permission': 'not_proven_by_read_access'}
    else:
        service = WechatPublishService(settings, logging.getLogger(__name__))
        if args.job:
            token = service._get_access_token()
            report = query_job(args.job, account=settings.account_key,
                               post=lambda endpoint, payload: service._wechat_post_json(endpoint, payload, access_token=token))
            report = {**report, 'this_check_publication_attempted': False}
        else:
            report = service.check_official_permissions()
    report.update(account_binding=binding, execution_host=os.environ.get('WECHAT_EXECUTION_HOST', 'local'),
                  api_host=urlsplit(settings.wechat.api_base_url).hostname,
                  proxy_configured=bool(settings.wechat.serv00_proxy_enabled or settings.wechat.api_proxy))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get('status') in {'observed', 'submitted_for_review', 'platform_reported_published'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
