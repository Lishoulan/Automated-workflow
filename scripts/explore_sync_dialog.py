"""临时脚本：探索135编辑器同步公众号对话框结构"""
from playwright.sync_api import sync_playwright
import time

profile_dir = r'F:\Automated-workflow-main\.runtime\accounts\robot-notes\.runtime\editor_135_profile'

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir, headless=False, channel='msedge'
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto('https://www.135editor.com/beautify_editor.html', wait_until='domcontentloaded', timeout=30000)
    time.sleep(5)

    # Click sync_official_accounts button
    btn = page.query_selector('#sync_official_accounts')
    if btn:
        btn.click()
        time.sleep(5)
        print('=== Sync dialog opened ===')

        # Find all visible elements in the dialog
        elements = page.query_selector_all('input, button, a, select, textarea, img, div[class*="cover"], div[class*="upload"]')
        for el in elements:
            try:
                if not el.is_visible():
                    continue
                tag = el.evaluate("el => el.tagName")
                typ = el.evaluate("el => el.type || ''")
                eid = el.evaluate("el => el.id")
                cls = el.evaluate("el => (el.className || '').substring(0,80)")
                ph = el.evaluate("el => el.placeholder || ''")
                accept = el.evaluate("el => el.accept || ''")
                src = el.evaluate("el => el.src || ''")[:60] if tag == 'IMG' else ''
                text = ""
                if tag in ("BUTTON", "A", "DIV"):
                    text = el.inner_text().strip()[:40]
                print(f"tag={tag} type={typ} id={eid} class={cls} ph={ph} text={text} accept={accept} src={src}")
            except Exception as ex:
                pass

        # Check for modals/overlays
        modals = page.query_selector_all('.modal, .dialog, [role=dialog], .modal-dialog, .modal-content, .layui-layer, .overlay')
        for m in modals:
            try:
                if m.is_visible():
                    cls = m.evaluate("el => (el.className || '').substring(0,80)")
                    eid = m.evaluate("el => el.id")
                    print(f"Modal: id={eid} class={cls}")
            except:
                pass

        # Take screenshot
        page.screenshot(path=r'F:\Automated-workflow-main\output\accounts\robot-notes\debug\sync_dialog.png')
        print('Screenshot saved')
    else:
        print("sync_official_accounts button not found")

    browser.close()
    print('Done')
