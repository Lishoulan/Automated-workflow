import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.services import wechat_official_publish as official
from toutiao_content_factory.services.wechat_service import WechatPublishService


def test_cloud_workflow_api_check_is_explicit_read_only_and_jobs_have_own_cache():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/wechat-daily-run.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    assert workflow['on']['workflow_dispatch']['inputs']['api_check']['default'] == 'false'
    job = workflow['jobs']['wechat']
    assert job['env']['WECHAT_OFFICIAL_PUBLISH_ENABLED'] == '0'
    probe = next(step for step in job['steps'] if 'check_wechat_official_api.py' in step.get('run', ''))
    assert probe['env']['WECHAT_APP_ID'] == '${{ secrets.WECHAT_APP_ID }}'
    assert probe['env']['WECHAT_APP_SECRET'] == '${{ secrets.WECHAT_APP_SECRET }}'
    assert '"$RUN_MODE" != "check"' in probe['run']
    assert '--expected-app-id-sha256 da6831610f26448462cb3393098f77e55671032ed95fd0ac29e280321715a66a' in probe['run']
    assert "inputs.api_check != true" in next(step for step in job['steps'] if 'wechat_cloud_run.py' in step.get('run', ''))['if']
    caches = [s for s in job['steps'] if 'actions/cache' in s.get('uses', '')]
    own = [s for s in caches if s.get('with', {}).get('path') == 'output/automation/wechat_official_jobs/']
    assert len(own) == 2 and 'github.ref_name' in own[0]['with']['key']
    original = next(s for s in caches if s.get('name') == 'Restore encrypted session and accepted history')
    assert 'wechat_official_jobs' not in original['with']['path']


def expected():
    return {'account': '小椰科技', 'title': '已审核文章', 'body_hash': 'a' * 64,
            'submitted_at': '2026-10-20T09:00:00+08:00'}


def test_formal_job_denies_without_authorization_before_any_io(tmp_path):
    path = tmp_path / 'job.json'
    post = MagicMock()
    with pytest.raises(PermissionError):
        official.submit_job(path, post=post, account='default', draft_media_id='draft',
                            expected=expected())
    assert not path.exists()
    post.assert_not_called()


def test_submit_persists_intent_and_id_then_resume_only_queries(tmp_path):
    path = tmp_path / 'job.json'
    def post(endpoint, payload):
        assert json.loads(path.read_text(encoding='utf-8'))['status'] == 'publish_uncertain'
        assert endpoint == '/cgi-bin/freepublish/submit' and payload == {'media_id': 'draft'}
        return {'publish_id': 'job-1'}
    first = official.submit_job(path, post=post, account='default', draft_media_id='draft',
                                expected=expected(), authorized=True)
    assert first['status'] == 'submitted_for_review' and first['publish_id'] == 'job-1'
    assert first['publication_confirmed'] is False
    never = MagicMock(side_effect=AssertionError('Must not submit twice'))
    assert official.submit_job(path, post=never, account='default', draft_media_id='draft',
                               expected=expected(), authorized=True)['publish_id'] == 'job-1'
    get = MagicMock(return_value={'publish_status': 0, 'article_detail': {'item': [
        {'article_url': 'https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1&sn=sig'}]}})
    result = official.query_job(path, post=get, account='default')
    assert get.call_args.args == ('/cgi-bin/freepublish/get', {'publish_id': 'job-1'})
    assert result['status'] == 'platform_reported_published'
    assert result['article_urls'] and result['publication_confirmed'] is False
    assert result['expected_publication'] == expected()


@pytest.mark.parametrize('response', [TimeoutError('private upstream detail'), {}, {'errcode': 48001}])
def test_ambiguous_or_denied_submission_is_durable_and_never_retried(tmp_path, response):
    path = tmp_path / 'job.json'
    post = MagicMock(side_effect=response) if isinstance(response, Exception) else MagicMock(return_value=response)
    first = official.submit_job(path, post=post, account='default', draft_media_id='draft',
                                expected=expected(), authorized=True)
    assert first['status'] in {'publish_uncertain', 'api_rejected'}
    assert 'private upstream detail' not in path.read_text(encoding='utf-8')
    official.submit_job(path, post=post, account='default', draft_media_id='draft',
                        expected=expected(), authorized=True)
    assert post.call_count == 1


def test_job_identity_and_corruption_fail_closed(tmp_path):
    path = tmp_path / 'job.json'
    official.submit_job(path, post=lambda *args: {'publish_id': 'one'}, account='default',
                        draft_media_id='draft', expected=expected(), authorized=True)
    for change in [{'account': 'other'}, {'draft_media_id': 'another'},
                   {'expected': {**expected(), 'body_hash': 'b' * 64}}]:
        args = dict(account='default', draft_media_id='draft', expected=expected()) | change
        with pytest.raises(ValueError):
            official.submit_job(path, post=MagicMock(), authorized=True, **args)
    path.write_text('broken')
    with pytest.raises(ValueError):
        official.query_job(path, post=MagicMock(), account='default')


def test_realistic_query_urls_reject_fake_hosts_and_identity_mismatch():
    response = {'publish_status': 0, 'publish_id': 'other', 'article_detail': {'item': []}}
    with pytest.raises(ValueError):
        official.publication_status(response, publish_id='one')
    response.pop('publish_id')
    response['article_detail']['item'] = [
        {'article_url': 'https://mp.weixin.qq.com.evil.test/s/a'},
        {'article_url': 'https://user@mp.weixin.qq.com/s/a'},
        {'article_url': 'https://mp.weixin.qq.com/s?__biz=a&mid=2&idx=1&sn=c'},
    ]
    assert official.publication_status(response, publish_id='one')['article_urls'] == [
        'https://mp.weixin.qq.com/s?__biz=a&idx=1&mid=2&sn=c']


def test_official_api_probe_uses_read_endpoints_only(app_settings, logger, monkeypatch):
    object.__setattr__(app_settings.wechat, 'app_id', 'test-account')
    object.__setattr__(app_settings.wechat, 'app_secret', 'test-secret')
    service = WechatPublishService(app_settings, logger)
    monkeypatch.setattr(service, '_get_access_token', lambda: 'private-token')
    post = MagicMock(side_effect=[{'total_count': 1}, {'total_count': 0, 'item': []}])
    monkeypatch.setattr(service, '_wechat_post_json', post)
    report = service.check_official_permissions()
    assert report['draft_read'] == 'available' and report['freepublish_read'] == 'available'
    assert report['formal_submit_permission'] == 'not_proven_by_read_access'
    assert report['publication_attempted'] is False
    assert [c.args[0] for c in post.call_args_list] == ['/cgi-bin/draft/batchget', '/cgi-bin/freepublish/batchget']
    assert 'private-token' not in json.dumps(report)


def test_cloud_probe_requires_target_app_id_before_any_api_call(app_settings, monkeypatch, tmp_path):
    from scripts import check_wechat_official_api as probe
    object.__setattr__(app_settings.wechat, 'app_id', 'test-account')
    object.__setattr__(app_settings.wechat, 'app_secret', 'test-secret')
    monkeypatch.setattr(probe, 'load_settings', lambda *args, **kwargs: app_settings)
    service = MagicMock()
    service.check_official_permissions.return_value = {'status': 'observed', 'draft_read': 'available',
                                                       'freepublish_read': 'available',
                                                       'formal_submit_permission': 'not_proven_by_read_access'}
    factory = MagicMock(return_value=service)
    monkeypatch.setattr(probe, 'WechatPublishService', factory)
    monkeypatch.setenv('WECHAT_EXECUTION_HOST', 'cloud')
    output = tmp_path / 'probe.json'
    for args, binding, status in [([], 'not_independently_verified', 'account_binding_unverified'),
                                  (['--expected-app-id-sha256', '0' * 64], 'mismatch', 'account_binding_mismatch')]:
        assert probe.main([*args, '--output', str(output)]) == 2
        result = json.loads(output.read_text(encoding='utf-8'))
        assert result['status'] == status
        assert result['account_binding'] == binding
        factory.assert_not_called()

    import hashlib
    expected = hashlib.sha256(b'test-account').hexdigest()
    assert probe.main(['--expected-app-id-sha256', expected, '--output', str(output)]) == 0
    service.check_official_permissions.assert_called_once_with()
    service.publish_run.assert_not_called()
    assert json.loads(output.read_text(encoding='utf-8'))['formal_submit_permission'] == 'not_proven_by_read_access'


def test_read_endpoint_whitelist_rejection_is_not_a_green_probe(app_settings, logger, monkeypatch):
    object.__setattr__(app_settings.wechat, 'app_id', 'test-account')
    object.__setattr__(app_settings.wechat, 'app_secret', 'test-secret')
    service = WechatPublishService(app_settings, logger)
    monkeypatch.setattr(service, '_get_access_token', lambda: 'private-token')
    post = MagicMock(side_effect=RuntimeError("WeChat API error: {'errcode': 40164, 'errmsg': 'invalid ip 203.0.113.7'}"))
    monkeypatch.setattr(service, '_wechat_post_json', post)
    report = service.check_official_permissions()
    assert report['status'] == 'ip_whitelist_blocked'
    assert report['observed_egress_ip'] == '203.0.113.7'
    assert report['formal_submit_permission'] == 'not_proven_by_read_access'
    assert report['draft_saving_attempted'] is False and report['publication_attempted'] is False
    assert [c.args[0] for c in post.call_args_list] == ['/cgi-bin/draft/batchget', '/cgi-bin/freepublish/batchget']


def test_whitelist_block_stops_on_first_egress_without_rotating(app_settings, logger, monkeypatch):
    object.__setattr__(app_settings.wechat, 'serv00_proxy_enabled', True)
    service = WechatPublishService(app_settings, logger)
    read = MagicMock(side_effect=RuntimeError('40164 invalid ip 203.0.113.7, not in whitelist'))
    monkeypatch.setattr(service, '_read_json_response', read)
    from toutiao_content_factory.services.wechat_service import WechatIpWhitelistBlockedException
    with pytest.raises(WechatIpWhitelistBlockedException) as error:
        service._get_access_token()
    assert error.value.ip_address == '203.0.113.7'
    assert read.call_count == 1


def test_official_transport_respects_shared_verification_deadline(app_settings, logger, monkeypatch):
    import time
    from urllib.request import Request
    service = WechatPublishService(app_settings, logger)
    service._api_deadline = time.monotonic() - 1
    opener = MagicMock()
    monkeypatch.setattr(service, '_build_opener', lambda: opener)
    with pytest.raises(TimeoutError):
        service._read_json_response(Request('https://api.weixin.qq.com/cgi-bin/token'))
    opener.open.assert_not_called()


@pytest.fixture
def prepared_service(app_settings, logger, monkeypatch):
    import time
    from toutiao_content_factory.services.wechat_execution import SUBMISSION_MIN_SECONDS
    # These service cases supply a valid isolated submission window. Earlier
    # cloud scenarios can leave their deliberately exhausted deadline behind.
    monkeypatch.setenv('WECHAT_RUN_DEADLINE_EPOCH', str(time.time() + SUBMISSION_MIN_SECONDS + 60))
    app_settings.platform = 'wechat'
    app_settings.wechat = replace(app_settings.wechat, enabled=True, auto_publish=True, account_name='小椰科技')
    app_settings.ensure_directories()
    run = app_settings.runs_dir / 'approved'
    publish = run / app_settings.review.publish_dirname
    publish.mkdir(parents=True)
    (run / 'review_status.json').write_text('{"status":"approved"}')
    (publish / 'publish_meta.json').write_text(json.dumps({'selected_title': '实际标题', 'approval_mode': 'manual'}))
    body = '已核对素材中的具体事实，结论保留适用条件。' * 25
    (publish / 'article.md').write_text(body, encoding='utf-8')
    service = WechatPublishService(app_settings, logger)
    monkeypatch.setattr(service, '_get_access_token', MagicMock(return_value='private'))
    monkeypatch.setattr(service, '_resolve_image_assets', lambda *a, **kw: (publish / 'cover.png', []))
    monkeypatch.setattr(service, '_upload_cover_material', lambda *a: 'cover')
    monkeypatch.setattr(service, '_render_article_html_via_backend', lambda **kw: ('<p>'+body+'</p>', {}))
    monkeypatch.setattr(service.growth_ops_service, 'register_publish_event', MagicMock(return_value={}))
    monkeypatch.setattr(service, '_submit_draft_via_browser', MagicMock(side_effect=AssertionError('No browser formal submission')))
    monkeypatch.setattr(service, '_publish_via_browser_only', MagicMock(return_value={'status': 'draft_saved', 'app_msg_id': 'browser-draft'}))
    return service, run


def test_service_formal_submission_is_off_by_default(prepared_service):
    service, run = prepared_service
    with pytest.raises(PermissionError):
        service.publish_run(run, submit=True)
    service._get_access_token.assert_not_called()


def test_service_routes_authorized_api_and_resumes_without_a_new_draft(prepared_service, monkeypatch):
    service, run = prepared_service
    object.__setattr__(service.settings.wechat, 'official_publish_enabled', True)
    post = MagicMock(side_effect=[{'media_id': 'api-draft'}, {'publish_id': 'job-1'}, {'publish_status': 1}])
    monkeypatch.setattr(service, '_wechat_post_json', post)
    exports = service.publish_run(run, submit=True)
    first = json.loads(exports['wechat_publish_result'].read_text(encoding='utf-8'))
    assert first['status'] == 'submitted_for_review' and first['publish_id'] == 'job-1'
    assert first['publication_confirmed'] is False
    service.publish_run(run, submit=True)
    assert [c.args[0] for c in post.call_args_list] == [
        '/cgi-bin/draft/add', '/cgi-bin/freepublish/submit', '/cgi-bin/freepublish/get']
    service._submit_draft_via_browser.assert_not_called()
    service._publish_via_browser_only.assert_not_called()


def test_unavailable_official_api_delivers_only_a_human_review_draft(prepared_service):
    service, run = prepared_service
    object.__setattr__(service.settings.wechat, 'official_publish_enabled', True)
    service._get_access_token.side_effect = RuntimeError('API unavailable')
    exports = service.publish_run(run, submit=True)
    result = json.loads(exports['wechat_publish_result'].read_text(encoding='utf-8'))
    assert result['status'] == 'draft_saved' and result['manual_publication_required'] is True
    assert result['publication_attempted'] is False
    assert service._publish_via_browser_only.call_args.kwargs['submit'] is False


def test_draft_handoff_binds_delivered_body_without_authorizing_publication(prepared_service):
    service, run = prepared_service
    object.__setattr__(service.settings.wechat, 'draft_only', True)
    service._get_access_token.side_effect = RuntimeError('API unavailable')
    result = json.loads(service.publish_run(run)['wechat_publish_result'].read_text(encoding='utf-8'))
    handoff = result['draft_handoff']
    assert handoff['account'] == '小椰科技' and len(handoff['body_hash']) == 64
    assert handoff['saved_at'] and handoff['body_hash_basis'] == 'rendered_delivery_payload'
    assert 'expected_publication' not in result and result['publication_attempted'] is False


def test_corrupt_history_is_preserved_instead_of_silently_reset(prepared_service):
    service, run = prepared_service
    service.history_path.write_text('corrupt history')
    with pytest.raises(ValueError):
        service._append_history({'status': 'draft_saved'})
    assert service.history_path.read_text(encoding='utf-8') == 'corrupt history'


def test_official_public_proof_needs_same_job_account_body_and_url():
    from toutiao_content_factory.services.wechat_metrics import match_publication, body_hash
    body = '原文事实及判断边界。' * 40
    expect = {**expected(), 'body_hash': body_hash(body), 'publish_id': 'job-1'}
    url = 'https://mp.weixin.qq.com/s?__biz=a&mid=123&idx=1&sn=b'
    backend = {'account': '小椰科技', 'title': expect['title'], 'status': 'platform_reported_published',
               'evidence_source': 'official_freepublish', 'publish_status': 0,
               'publish_id': 'job-1', 'url': url}
    article = {'account': '小椰科技', 'title': expect['title'], 'body': body,
               'url': url, 'published_at': '2026-10-20T09:15:00+08:00'}
    assert match_publication(expect, backend, article)['confirmed'] is True
    for change in [{'publish_id': 'other'}, {'publish_status': 1}, {'account': 'other'}]:
        assert match_publication(expect, {**backend, **change}, article) is None
    assert match_publication(expect, backend, {**article, 'body': body + 'changed'}) is None


def test_manual_draft_reconciliation_is_separate_from_auto_publication_counts(app_settings, monkeypatch):
    from scripts import wechat_cloud_run as cloud
    from toutiao_content_factory.services import wechat_metrics as metrics
    handoff = {**expected(), 'saved_at': expected()['submitted_at']}
    state = {'schema': 1, 'runs': [
        {'run_key': 'draft:1', 'mode': 'draft', 'started_at': expected()['submitted_at'], 'outcome': 'draft',
         'draft_delivery': {'status': 'saved', 'app_msg_id': 'one', 'handoff': handoff}},
        {'run_key': 'check:1', 'mode': 'check', 'started_at': expected()['submitted_at'], 'outcome': 'draft', 'excluded': True},
    ]}
    proof = {'confirmed': True, 'url': 'https://mp.weixin.qq.com/s/one'}
    verify = MagicMock(return_value={'draft:1': proof})
    monkeypatch.setattr(cloud, 'verify_pending', verify)
    formal, manual = cloud.reconcile_publications(app_settings, state)
    assert formal == {} and manual == {'draft:1': proof}
    assert state['runs'][0]['outcome'] == 'draft'
    assert state['runs'][0]['manual_publication'] == proof
    assert metrics.summary(state)['30']['actual_publish_runs'] == 0
    verify.reset_mock()
    cloud.reconcile_publications(app_settings, state)
    verify.assert_not_called()


def test_scheduled_draft_run_reconciles_human_publication_without_generating(app_settings, monkeypatch):
    from datetime import datetime, timezone
    from scripts import wechat_cloud_run as cloud
    now = datetime.now(timezone.utc).isoformat()
    handoff = {**expected(), 'saved_at': now}
    state = {'schema': 1, 'runs': [{'run_key': '101:1', 'mode': 'draft', 'started_at': now,
        'outcome': 'draft', 'draft_delivery': {'status': 'saved', 'app_msg_id': 'one', 'handoff': handoff}}]}
    monkeypatch.setenv('GITHUB_RUN_ID', '102')
    monkeypatch.setattr(cloud, 'load_settings', lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, 'configure_environment', lambda *a, **kw: None)
    monkeypatch.setattr(cloud, 'restore_session', MagicMock())
    monkeypatch.setattr(cloud.xstate, 'read_remote_state', lambda *a, **kw: state if kw.get('path') == cloud.metrics.METRICS_PATH else {})
    monkeypatch.setattr(cloud, 'write_state_file', MagicMock())
    proof = {'confirmed': True, 'url': 'https://mp.weixin.qq.com/s/one'}
    verify = MagicMock(return_value={'101:1': proof})
    monkeypatch.setattr(cloud, 'verify_pending', verify)
    child = MagicMock(side_effect=AssertionError('Already drafted: no generation or delivery'))
    monkeypatch.setattr(cloud.subprocess, 'run', child)
    assert cloud.main(['--mode', 'draft']) == 0
    verify.assert_called_once()
    child.assert_not_called()
    result = json.loads((app_settings.automation_dir / 'wechat_cloud_result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'already_drafted'
    assert result['manual_publications_verified'] == [proof]
    assert result['formal_publication_confirmed'] is False


def test_native_transport_preserves_explicit_api_denial_without_provider_text(app_settings, logger, monkeypatch, tmp_path):
    service = WechatPublishService(app_settings, logger)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = b'{"errcode":48001,"errmsg":"private upstream detail"}'
    monkeypatch.setattr(service, '_build_opener', lambda: opener)
    path = tmp_path / 'job.json'
    receipt = official.submit_job(path, account='default', draft_media_id='draft', expected=expected(), authorized=True,
        post=lambda endpoint, payload: service._wechat_post_json(endpoint, payload, access_token='test'))
    assert receipt['status'] == 'api_rejected' and receipt['api_error_code'] == 48001
    assert 'private upstream detail' not in path.read_text(encoding='utf-8')


def test_draft_creation_failure_cannot_enable_browser_formal_publication(prepared_service, monkeypatch):
    service, run = prepared_service
    object.__setattr__(service.settings.wechat, 'official_publish_enabled', True)
    monkeypatch.setattr(service, '_wechat_post_json', MagicMock(return_value={'ret': '200002'}))
    exports = service.publish_run(run, submit=True)
    assert service._publish_via_browser_only.call_args.kwargs['submit'] is False
    result = json.loads(exports['wechat_publish_result'].read_text(encoding='utf-8'))
    assert result['draft_only'] is True and result['publication_attempted'] is False
    assert 'expected_publication' not in result
