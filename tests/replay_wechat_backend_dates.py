"""Offline Chromium replay of the actual homepage date markup; no login/network."""
import sys
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from toutiao_content_factory.services.wechat_publication_verifier import BACKEND_RECORDS_JS
from toutiao_content_factory.services.wechat_metrics import body_hash, match_publication

with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='msedge' if sys.platform == 'win32' else None,headless=True)
    try:
        page=browser.new_page()
        page.route('**/*',lambda route:route.abort())
        page.set_content('''<div class="account_box-panel-head__nickname">小椰科技</div>
          <div class="publish_hover_content"><div>今天 09:57</div><div>已发表</div><a href="https://mp.weixin.qq.com/s/today">当前文章</a></div>
          <div class="publish_hover_content"><div>10月09日</div><div>已发表</div><a href="https://mp.weixin.qq.com/s/old">原日期文章</a></div>
          <div class="publish_hover_content"><div>星期五 17:24</div><div>已发表</div><a href="https://mp.weixin.qq.com/s/weekly">不推定日期</a></div>''')
        page.evaluate("""() => {const OriginalDate=Date;globalThis.Date=class extends OriginalDate {
          constructor(...args){super(...(args.length?args:['2026-10-20T02:30:00Z']))}
          static now(){return new OriginalDate('2026-10-20T02:30:00Z').getTime()}}} """)
        result=page.evaluate(BACKEND_RECORDS_JS)
        rows=result['records']
        assert [r['published_at'] for r in rows]==['10月20日','10月09日',''],result
        # The UTC date is still the 19th while the backend's 今天 is the 20th.
        page.evaluate("() => {Date.now=()=>Date.parse('2026-10-19T17:30:00Z')}")
        assert page.evaluate(BACKEND_RECORDS_JS)['records'][0]['published_at']=='10月20日'
        body='真实正文的具体内容以及事实范围。'*30
        expected={'account':'小椰科技','title':'当前文章','body_hash':body_hash(body),'submitted_at':'2026-10-20T09:00:00+08:00'}
        article={'account':'小椰科技','title':'当前文章','url':'https://mp.weixin.qq.com/s/today','body':body,'published_at':'2026-10-20T09:58:14+08:00'}
        assert match_publication(expected,rows[0],article)['confirmed'] is True
        assert match_publication(expected,rows[0],{**article,'body':body+'无来源新事实'}) is None
        assert match_publication(expected,rows[0],{**article,'published_at':'2025-10-20T09:58:14+08:00'}) is None
        assert match_publication(expected,rows[0],{**article,'account':'其他账号'}) is None
        print(json.dumps({'status':'passed','network_requests':0,'publication_attempted':False,'date_cases':4,'public_match_checks':4,'records':rows},ensure_ascii=False))
    finally:browser.close()
