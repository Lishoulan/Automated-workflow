"""Fail-closed response handling and durable optional freepublish jobs.

API acceptance is an asynchronous publish job, never a verified public article.
The caller supplies the existing authenticated transport. A job receipt owns
submission intent and provider ID; reports never authorize another submission.
"""
from __future__ import annotations

from typing import Any
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import uuid

from toutiao_content_factory.services.wechat_growth_service import article_url


class OfficialAPIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'Official WeChat API rejected the request: errcode={code}')


def _check_response(response):
    if not isinstance(response, dict):
        raise ValueError('Invalid official API response')
    raw = response.get('errcode', 0)
    if isinstance(raw, bool):
        raise ValueError('Invalid official API error code')
    code = int(raw or 0)
    if code:
        raise OfficialAPIError(code)


def submission_receipt(response: dict[str, Any], *, draft_media_id: str) -> dict[str, Any]:
    if not draft_media_id:
        raise ValueError("Cannot submit an unidentified WeChat draft")
    _check_response(response)
    raw = response.get('publish_id')
    publish_id = str(raw).strip() if type(raw) in (str, int) else ''
    if not publish_id:
        raise RuntimeError("Missing publish_id: submission outcome is uncertain; do not retry")
    return {"status": "submitted_for_review", "method": "official_api",
            "draft_media_id": draft_media_id, "publish_id": publish_id,
            "publication_confirmed": False, "publish_url": ""}


def publication_status(response: dict[str, Any], *, publish_id: str) -> dict[str, Any]:
    """Read-only status projection; public verification is a separate step."""
    if not publish_id:
        raise ValueError("publish_id is required")
    _check_response(response)
    if 'publish_id' in response and str(response['publish_id']) != publish_id:
        raise ValueError('Official publish job identity mismatch')
    raw = response.get("publish_status")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in range(7):
        raise ValueError("Unknown WeChat publish status; keep pending")
    urls = [cleaned
            for item in (response.get("article_detail") or {}).get("item", [])
            if isinstance(item, dict) and (cleaned := article_url(item.get('article_url', '')))]
    # Status zero means WeChat reports publication, not that article/body was independently matched.
    return {"publish_id": publish_id, "publish_status": raw,
            "status": "platform_reported_published" if raw == 0 else
                      "submitted_for_review" if raw == 1 else "publish_failed_or_removed",
            "article_urls": urls, "publication_confirmed": False}


def _read_job(path, account):
    job = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(job, dict) or job.get('schema') != 1 or job.get('account_key') != account
            or not job.get('draft_media_id') or not isinstance(job.get('expected_publication'), dict)):
        raise ValueError('Invalid or differently scoped official publish job')
    return job


def _save_job(path, job):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(job, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def submit_job(path, *, post, account, draft_media_id, expected, authorized=False):
    """One request per immutable job. Unknown outcomes are never resubmitted."""
    if authorized is not True:
        raise PermissionError('Official formal publication requires explicit authorization')
    if not account or not draft_media_id or not all(expected.get(k) for k in ('account', 'title', 'body_hash', 'submitted_at')):
        raise ValueError('Incomplete official submission identity')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    job = {'schema': 1, 'account_key': account, 'draft_media_id': draft_media_id,
           'expected_publication': dict(expected), 'status': 'publish_uncertain', 'method': 'official_api',
           'publication_attempted': True, 'publication_confirmed': False, 'publish_url': '',
           'created_at': datetime.now(timezone.utc).isoformat()}
    try:
        with path.open('x', encoding='utf-8') as handle:
            json.dump(job, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        prior = _read_job(path, account)
        if (prior['draft_media_id'] != draft_media_id or any(
                prior['expected_publication'].get(k) != expected[k] for k in ('account', 'title', 'body_hash'))):
            raise ValueError('Existing official job does not match this manuscript')
        return prior
    try:
        response = post('/cgi-bin/freepublish/submit', {'media_id': draft_media_id})
        job.update(submission_receipt(response, draft_media_id=draft_media_id))
    except OfficialAPIError as exc:
        job.update(status='api_rejected', api_error_code=exc.code)
    except Exception as exc:
        job['error_type'] = type(exc).__name__  # Never persist provider messages or credentials.
    _save_job(path, job)
    return job


def query_job(path, *, post, account, expected=None):
    """Read the existing provider ID, including after a process restart."""
    job = _read_job(path, account)
    if expected and any(job['expected_publication'].get(k) != v for k, v in expected.items()):
        raise ValueError('Official job manuscript identity mismatch')
    if not job.get('publish_id'):
        return job
    try:
        response = post('/cgi-bin/freepublish/get', {'publish_id': job['publish_id']})
        job.update(publication_status(response, publish_id=job['publish_id']))
        job['query_status'] = 'ok'
        job.pop('last_query_error', None)
    except Exception as exc:
        job.update(query_status='unavailable', last_query_error=type(exc).__name__)
    _save_job(path, job)
    return job
