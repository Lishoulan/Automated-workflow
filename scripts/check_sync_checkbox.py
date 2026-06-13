"""临时脚本：检查保存图文对话框中复选框状态"""
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

    # Fill title
    title_input = page.query_selector('#edui52_wx_input')
    if title_input:
        title_input.fill('测试同步功能')

    # Click save-as-template
    btn = page.query_selector('#save-as-template')
    if btn:
        btn.click()
        time.sleep(3)

        # Find all checkboxes in the save dialog
        dialog = page.query_selector('#save-wx-msg-dialog')
        if dialog:
            checkboxes = dialog.query_selector_all('input[type="checkbox"]')
            for i, cb in enumerate(checkboxes):
                try:
                    eid = cb.evaluate("el => el.id")
                    name = cb.evaluate("el => el.name")
                    cls = cb.evaluate("el => (el.className || '').substring(0,60)")
                    checked = cb.is_checked()
                    # Find label
                    parent_text = ""
                    try:
                        parent = cb.evaluate_handle("el => el.parentElement")
                        parent_text = parent.inner_text().strip()[:60]
                    except:
                        pass
                    print(f"Checkbox {i}: id={eid} name={name} class={cls} checked={checked} parent_text={parent_text}")
                except Exception as ex:
                    print(f"Checkbox {i}: error={ex}")

            # Also find all labels with text containing sync/同步
            labels = dialog.query_selector_all('label, span, div')
            for lbl in labels:
                try:
                    if not lbl.is_visible():
                        continue
                    text = lbl.inner_text().strip()
                    if text and ('同步' in text or '小椰' in text or '公众号' in text):
                        tag = lbl.evaluate("el => el.tagName")
                        cls = lbl.evaluate("el => (el.className || '').substring(0,60)")
                        # Check if it has a checkbox inside
                        has_cb = lbl.query_selector('input[type="checkbox"]') is not None
                        print(f"Sync label: tag={tag} class={cls} has_checkbox={has_cb} text={text[:80]}")
                except:
                    pass

            # Take screenshot
            page.screenshot(path=r'F:\Automated-workflow-main\output\accounts\robot-notes\debug\save_dialog_checkboxes.png')
            print('Screenshot saved')
        else:
            print("Save dialog not found")
    else:
        print("save-as-template button not found")

    browser.close()
    print('Done')
