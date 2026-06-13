"""临时脚本：探索135编辑器同步公众号对话框内部结构"""
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

        # Get the dialog HTML
        dialog = page.query_selector('#sync_to_wechat-dialog')
        if dialog:
            # Get all elements inside the dialog
            elements = dialog.query_selector_all('input, button, a, select, textarea, img, div, span, label')
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
                    text = ""
                    if tag in ("BUTTON", "A", "SPAN", "LABEL", "DIV"):
                        try:
                            text = el.inner_text().strip()[:50]
                        except:
                            pass
                    if tag == "INPUT" or tag == "BUTTON" or tag == "SELECT" or text or accept:
                        print(f"tag={tag} type={typ} id={eid} class={cls} ph={ph} text={text} accept={accept}")
                except Exception as ex:
                    pass

        # Take screenshot
        page.screenshot(path=r'F:\Automated-workflow-main\output\accounts\robot-notes\debug\sync_dialog_detail.png')
        print('Screenshot saved')
    else:
        print("sync_official_accounts button not found")

    browser.close()
    print('Done')
