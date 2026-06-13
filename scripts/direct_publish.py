import sys, os, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger('direct')

from toutiao_content_factory.config import load_settings
settings = load_settings(Path(__file__).resolve().parent.parent, account_key='default')

test_key = 'direct_pub_' + time.strftime('%Y%m%d_%H%M%S')
run_dir = settings.output_dir / 'runs' / test_key
(run_dir / 'publish').mkdir(parents=True, exist_ok=True)
(run_dir / 'images').mkdir(exist_ok=True)

publish_meta = {
    'selected_title': 'AI芯片新突破：存算一体架构颠覆传统计算',
    'selected_title_index': 0,
    'summary': '中国AI芯片团队发布全球首款存算一体大模型推理芯片',
    'tags': ['#AI芯片', '#存算一体'],
    'selected_topic': {'title': 'AI芯片技术突破', 'url': ''},
    'style_key': 'tech_media',
    'score_snapshot': {'overall_score': 80},
}
(run_dir / 'publish' / 'publish_meta.json').write_text(json.dumps(publish_meta, ensure_ascii=False), encoding='utf-8')

article = '<p>China AI chip startup announces world first LLM inference chip using compute-in-memory architecture, achieving 15x energy efficiency over traditional GPU.</p><p>This breakthrough marks a significant advancement in AI chip technology.</p>'
(run_dir / 'publish' / 'article.md').write_text(article, encoding='utf-8')
(run_dir / 'review_status.json').write_text(json.dumps({'status': 'approved', 'reviewed_at': 'now'}), encoding='utf-8')

logger.info('=== Starting direct publish test ===')
from toutiao_content_factory.services.publish_service import PublishService
svc = PublishService(settings, logger)

try:
    result = svc.publish_run(run_dir, declare_first_release=False)
    result_path = result.get('publish_result', '')
    if result_path:
        data = json.loads(Path(result_path).read_text(encoding='utf-8'))
        logger.info('=== PUBLISH RESULT ===')
        logger.info('Status: %s', data.get('status'))
        backend = data.get('backend_response_summary', {})
        logger.info('pgc_id: %s', backend.get('pgc_id', 'N/A'))
        logger.info('Backend message: %s', backend.get('message', 'N/A'))
        for n in data.get('notes', []):
            logger.info('  - %s', n)
        logger.info('=== DONE ===')
except Exception as e:
    logger.error('FAILED: %s', e)
    import traceback; traceback.print_exc()
