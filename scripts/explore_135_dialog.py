"""临时脚本：探索135编辑器保存同步对话框结构"""
from playwright.sync_api import sync_playwright
import time
import json

profile_dir = r'F:\Automated-workflow-main\.runtime\accounts\robot-notes\.runtime\editor_135_profile'

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir, headless=False, channel='msedge'
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto('https://www.135editor.com/beautify_editor.html', wait_until='domcontentloaded', timeout=30000)
    time.sleep(5)

    btn = page.query_selector('#save-as-template')
    if btn:
        btn.click()
        time.sleep(3)
        print('=== Dialog opened ===')

        elements = page.query_selector_all('input, button, a.btn, select, textarea')
        for el in elements:
            try:
                if not el.is_visible():
                    continue
                tag = el.evaluate("el => el.tagName")
                typ = el.evaluate("el => el.type || ''")
                eid = el.evaluate("el => el.id")
                cls = el.evaluate("el => (el.className || '').substring(0,60)")
                ph = el.evaluate("el => el.placeholder || ''")
                accept = el.evaluate("el => el.accept || ''")
                text = ""
                if tag in ("BUTTON", "A"):
                    text = el.inner_text().strip()[:30]
                print(f"tag={tag} type={typ} id={eid} class={cls} placeholder={ph} text={text} accept={accept}")
            except Exception as ex:
                pass

        # Also check for modals
        modals = page.query_selector_all('.modal, .dialog, [role=dialog], .modal-dialog, .modal-content, .layui-layer')
        for m in modals:
            try:
                if m.is_visible():
                    cls = m.evaluate("el => (el.className || '').substring(0,80)")
                    eid = m.evaluate("el => el.id")
                    print(f"Modal: id={eid} class={cls}")
            except:
                pass
    else:
        print("Save-as-template button not found")

    browser.close()
    print('Done')
