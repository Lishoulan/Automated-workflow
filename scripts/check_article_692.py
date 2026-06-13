import sys, re, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from playwright.sync_api import sync_playwright
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check2")
settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")
profile_dir = settings.account_runtime_dir / "wechat_profile"
prepare_browser_profile(profile_dir=profile_dir, project_root=settings.project_root, logger=logger, label="check2", extra_paths=[settings.account_runtime_dir])
with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(user_data_dir=str(profile_dir), headless=False, channel="msedge")
    page = browser.pages[0] if browser.pages else browser.new_page()
    base = settings.wechat.base_url.rstrip("/")
    page.goto(base, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    token = re.search(r"token=(\d+)", page.url).group(1)
    edit_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&appmsgid=100000692&token={token}&lang=zh_CN"
    page.goto(edit_url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(8000)
    state = page.evaluate("""() => {
        const editor = document.querySelector('.ProseMirror');
        return {
            text: editor ? editor.textContent.trim().substring(0, 300) : '',
            html: editor ? editor.innerHTML.substring(0, 800) : '',
            wordCount: document.querySelector('.js_word_count')?.textContent?.trim() || 'N/A'
        };
    }""")
    print(json.dumps(state, ensure_ascii=False, indent=2))
    browser.close()
