"""精确分析135编辑器保存按钮"""
import sys, os, time
sys.path.insert(0, 'f:/Automated-workflow-main/src')
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.abspath("f:/Automated-workflow-main/.runtime/.runtime/editor_135_profile")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR, headless=False, channel="msedge",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto("https://www.135editor.com/beautify_editor.html", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # 填写标题和内容
        title_input = page.query_selector("#edui52_wx_input")
        if title_input:
            title_input.fill("测试保存按钮分析")
        page.evaluate("""() => {
            const instants = window.UE && window.UE.instants;
            if (!instants) return;
            for (const key of Object.keys(instants)) {
                const editor = instants[key];
                if (editor && editor.setContent) { editor.setContent('<p>测试内容</p>'); return; }
            }
        }""")

        # 点击保存同步
        save_btn = page.query_selector("#save-as-template")
        if save_btn:
            save_btn.click(force=True)
        page.wait_for_timeout(3000)

        dialog = page.query_selector("#save-wx-msg-dialog")
        if not dialog:
            print("保存对话框未出现")
            browser.close()
            return

        # 上传封面
        upload_btn = dialog.query_selector("text=上传封面图")
        if upload_btn:
            upload_btn.click()
            page.wait_for_timeout(1000)
            cover_path = os.path.abspath("f:/Automated-workflow-main/output/runs/20260606-070302_比亚迪玩跨界-进军人形机器人-代号-尧舜禹/publish/wechat/cover.png")
            if os.path.exists(cover_path):
                file_input = page.query_selector("input[type='file']")
                if file_input:
                    file_input.set_input_files(cover_path)
                    print("已上传封面图")
                    page.wait_for_timeout(3000)
                    # 裁剪
                    cropper = page.query_selector("#save-image-cropper")
                    if cropper and cropper.is_visible():
                        confirm_btn = cropper.query_selector(".btn-primary")
                        if confirm_btn:
                            confirm_btn.click(force=True)
                        page.wait_for_timeout(2000)
                        page.evaluate("$('#save-image-cropper').modal('hide') if window.$ && window.$.fn.modal else null")
                        page.wait_for_timeout(1000)
                    # 版权声明
                    page.wait_for_timeout(2000)
                    copyright_popup = page.query_selector("[id*='copyright_popup']")
                    if copyright_popup and copyright_popup.is_visible():
                        # 勾选下次不再提示
                        no_remind = copyright_popup.query_selector("input[type='checkbox']")
                        if no_remind and not no_remind.is_checked():
                            no_remind.click(force=True)
                            print("已勾选下次不再提示")
                        know_btn = copyright_popup.query_selector("button:has-text('我已知晓')")
                        if know_btn:
                            know_btn.click(force=True)
                            print("已点击我已知晓")
                        else:
                            close_btn = copyright_popup.query_selector(".close, .btn-primary")
                            if close_btn:
                                close_btn.click(force=True)
                        page.wait_for_timeout(1000)

        # 勾选同步
        sync_cb = page.query_selector("input[name='data[WxMsg][sync][]']")
        if sync_cb and not sync_cb.is_checked():
            sync_cb.click(force=True)
            print("已勾选同步")

        page.wait_for_timeout(1000)

        # 详细分析保存按钮
        print("\n=== 分析保存按钮 ===")
        btn_info = page.evaluate("""() => {
            const dialog = document.getElementById('save-wx-msg-dialog');
            if (!dialog) return 'no dialog';
            const results = [];
            // 查找所有按钮
            const allBtns = dialog.querySelectorAll('button, .btn, a.btn, input[type="submit"]');
            for (const btn of allBtns) {
                const rect = btn.getBoundingClientRect();
                const styles = window.getComputedStyle(btn);
                results.push({
                    tag: btn.tagName,
                    text: btn.innerText.trim().substring(0, 50),
                    className: btn.className.substring(0, 80),
                    id: btn.id || '',
                    type: btn.type || '',
                    visible: rect.width > 0 && rect.height > 0 && styles.display !== 'none' && styles.visibility !== 'hidden',
                    position: `x=${Math.round(rect.x)} y=${Math.round(rect.y)} w=${Math.round(rect.width)} h=${Math.round(rect.height)}`,
                    onclick: (btn.getAttribute('onclick') || '').substring(0, 80),
                    parentText: btn.parentElement ? btn.parentElement.innerText.trim().substring(0, 50) : '',
                });
            }
            return results;
        }""")
        for info in btn_info:
            print(f"  {info}")

        # 尝试用JS直接触发保存
        print("\n=== 尝试JS直接触发保存 ===")
        js_result = page.evaluate("""() => {
            const dialog = document.getElementById('save-wx-msg-dialog');
            if (!dialog) return 'no dialog';

            // 查找保存按钮 - 查找包含"保存"文字的可见按钮
            const allBtns = dialog.querySelectorAll('button, .btn');
            for (const btn of allBtns) {
                const rect = btn.getBoundingClientRect();
                const styles = window.getComputedStyle(btn);
                const isVisible = rect.width > 0 && rect.height > 0 && styles.display !== 'none';
                const text = btn.innerText.trim();
                if (isVisible && (text.includes('保存') || btn.classList.contains('btn-primary'))) {
                    btn.click();
                    return `clicked: tag=${btn.tagName} text='${text}' class='${btn.className.substring(0,50)}'`;
                }
            }
            return 'no save button found';
        }""")
        print(f"JS结果: {js_result}")

        # 等待并监控
        for i in range(20):
            page.wait_for_timeout(3000)
            # 检查进度
            progress = page.evaluate("""() => {
                const allEls = document.querySelectorAll('*');
                for (const el of allEls) {
                    const text = (el.innerText || '').trim();
                    if (/^\\d+%$/.test(text) && el.offsetParent !== null) {
                        return text;
                    }
                }
                return '';
            }""")
            dialog2 = page.query_selector("#save-wx-msg-dialog")
            dialog_vis = dialog2.is_visible() if dialog2 else False
            print(f"  [{i*3}s] progress='{progress}' dialog_visible={dialog_vis}")
            if not dialog_vis:
                print("对话框已关闭！")
                break

        browser.close()

main()
