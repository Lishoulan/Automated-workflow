"""
135编辑器保存对话框调试脚本
详细分析保存按钮和表单行为，排查点击后对话框不关闭的问题
"""
import os
import time
from playwright.sync_api import sync_playwright

DEBUG_DIR = r"f:\Automated-workflow-main\.runtime\debug"
PROFILE_DIR = r"f:\Automated-workflow-main\.runtime\accounts\robot-notes\editor_135_profile"
EDITOR_URL = "https://www.135editor.com/beautify_editor.html"

os.makedirs(DEBUG_DIR, exist_ok=True)


def screenshot(page, name):
    path = os.path.join(DEBUG_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"[截图] {path}")


def main():
    with sync_playwright() as p:
        print("=" * 60)
        print("启动浏览器...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="msedge",
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # ---- Step 1: 导航到编辑器 ----
        print("\n[Step 1] 导航到135编辑器...")
        page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        screenshot(page, "01_page_loaded")

        # ---- Step 2: 填写标题 ----
        print("\n[Step 2] 填写测试标题...")
        try:
            title_input = page.locator("#title-input, input[name='title'], .title-input input").first
            title_input.fill("测试文章")
            print("  标题已填写")
        except Exception as e:
            print(f"  标题填写失败: {e}")
            # 尝试通过JS填写
            page.evaluate("""() => {
                const el = document.querySelector('#title-input') || document.querySelector('input[name="title"]');
                if (el) { el.value = '测试文章'; el.dispatchEvent(new Event('input', {bubbles:true})); }
            }""")
        screenshot(page, "02_title_filled")

        # ---- Step 3: 设置编辑器内容 ----
        print("\n[Step 3] 通过UEditor API设置内容...")
        try:
            page.evaluate("""() => {
                if (window.UE && window.UE.getEditor) {
                    const editor = UE.getEditor('ueditor');
                    if (editor) {
                        editor.setContent('<p>这是测试内容，用于调试保存对话框问题。</p>');
                        return 'UEditor内容已设置';
                    }
                }
                return 'UEditor未找到';
            }""")
            print("  UEditor内容已设置")
        except Exception as e:
            print(f"  UEditor内容设置失败: {e}")
        screenshot(page, "03_content_set")

        # ---- Step 4: 点击"保存同步"按钮 ----
        print("\n[Step 4] 点击'保存同步'按钮 (#save-as-template)...")
        try:
            save_btn = page.locator("#save-as-template")
            save_btn.wait_for(state="visible", timeout=10000)
            save_btn.click()
            print("  已点击保存同步按钮")
        except Exception as e:
            print(f"  点击保存同步按钮失败: {e}")
            # 尝试JS点击
            page.evaluate("() => document.querySelector('#save-as-template')?.click()")
        time.sleep(2)
        screenshot(page, "04_after_click_save_as_template")

        # ---- Step 5: 等待保存对话框出现 ----
        print("\n[Step 5] 等待保存对话框 (#save-wx-msg-dialog) 出现...")
        try:
            dialog = page.locator("#save-wx-msg-dialog")
            dialog.wait_for(state="visible", timeout=10000)
            print("  对话框已出现")
        except Exception as e:
            print(f"  对话框未出现: {e}")
            # 尝试其他选择器
            alt_dialogs = page.evaluate("""() => {
                const dialogs = document.querySelectorAll('[class*="dialog"], [class*="modal"], [id*="dialog"], [id*="modal"]');
                return Array.from(dialogs).map(d => ({
                    id: d.id, className: d.className, display: getComputedStyle(d).display,
                    visibility: getComputedStyle(d).visibility, visible: d.offsetParent !== null
                }));
            }""")
            print(f"  页面上的对话框元素: {alt_dialogs}")
        screenshot(page, "05_dialog_appeared")

        # ---- Step 6: 检查同步复选框 ----
        print("\n[Step 6] 检查同步复选框...")
        try:
            sync_checkbox = page.evaluate("""() => {
                const cb = document.querySelector('#sync-wx, #sync-checkbox, input[name="sync"], input[type="checkbox"][id*="sync"]');
                if (cb) {
                    return { id: cb.id, name: cb.name, checked: cb.checked, type: cb.type };
                }
                // 列出对话框内所有checkbox
                const dialog = document.querySelector('#save-wx-msg-dialog');
                if (dialog) {
                    const checkboxes = dialog.querySelectorAll('input[type="checkbox"]');
                    return Array.from(checkboxes).map(c => ({
                        id: c.id, name: c.name, checked: c.checked, className: c.className
                    }));
                }
                return null;
            }""")
            print(f"  同步复选框状态: {sync_checkbox}")

            # 如果未勾选则勾选
            page.evaluate("""() => {
                const dialog = document.querySelector('#save-wx-msg-dialog');
                if (!dialog) return;
                const checkboxes = dialog.querySelectorAll('input[type="checkbox"]');
                checkboxes.forEach(cb => {
                    if (!cb.checked) {
                        cb.click();
                        console.log('勾选了复选框:', cb.id, cb.name);
                    }
                });
            }""")
        except Exception as e:
            print(f"  复选框检查失败: {e}")
        screenshot(page, "06_checkbox_checked")

        # ---- Step 7: 详细分析保存按钮和表单 ----
        print("\n" + "=" * 60)
        print("[Step 7] 详细分析保存按钮和表单...")
        print("=" * 60)

        # 7a: 查找所有表单
        print("\n--- 7a: 页面上所有表单 ---")
        all_forms = page.evaluate("""() => {
            const forms = document.querySelectorAll('form');
            return Array.from(forms).map((f, i) => ({
                index: i,
                id: f.id,
                action: f.action,
                method: f.method,
                target: f.target,
                className: f.className,
                parentId: f.parentElement?.id,
                inputs: Array.from(f.querySelectorAll('input, select, textarea')).map(el => ({
                    tag: el.tagName,
                    type: el.type,
                    name: el.name,
                    id: el.id,
                    value: el.value?.substring(0, 100),
                    className: el.className
                }))
            }));
        }""")
        for form in all_forms:
            print(f"\n  表单 #{form['index']}: id={form['id']}, action={form['action']}, method={form['method']}, target={form['target']}")
            print(f"    父元素id={form['parentId']}, class={form['className']}")
            for inp in form['inputs']:
                print(f"    输入: <{inp['tag']} type={inp['type']} name={inp['name']} id={inp['id']} value='{inp['value']}' class={inp['className']}>")

        # 7b: 分析 #save-wx-msg 元素
        print("\n--- 7b: #save-wx-msg 元素分析 ---")
        save_btn_info = page.evaluate("""() => {
            const el = document.querySelector('#save-wx-msg');
            if (!el) return { exists: false };
            const parentForm = el.closest('form');
            return {
                exists: true,
                tagName: el.tagName,
                type: el.type,
                name: el.name,
                value: el.value,
                form: el.form?.id || el.form?.action || 'null',
                formProperty: el.form ? { id: el.form.id, action: el.form.action, method: el.form.method } : null,
                insideForm: !!parentForm,
                parentFormId: parentForm?.id || null,
                parentFormAction: parentForm?.action || null,
                onclick: el.getAttribute('onclick'),
                outerHTML: el.outerHTML.substring(0, 500),
                computedDisplay: getComputedStyle(el).display,
                computedVisibility: getComputedStyle(el).visibility,
                rect: el.getBoundingClientRect()
            };
        }""")
        print(f"  {save_btn_info}")

        # 7c: jQuery事件处理器
        print("\n--- 7c: jQuery事件处理器 ---")
        jquery_events = page.evaluate("""() => {
            const el = document.querySelector('#save-wx-msg');
            if (!el) return { error: 'element not found' };

            const result = {};

            // jQuery data
            try {
                result.jqueryData = jQuery('#save-wx-msg').data();
            } catch(e) { result.jqueryDataError = e.message; }

            // jQuery events via _data
            try {
                const events = jQuery._data(el, 'events');
                if (events) {
                    result.jqueryEvents = {};
                    for (const [type, handlers] of Object.entries(events)) {
                        result.jqueryEvents[type] = handlers.map(h => ({
                            handler: h.handler?.toString()?.substring(0, 300),
                            selector: h.selector,
                            namespace: h.namespace
                        }));
                    }
                } else {
                    result.jqueryEvents = null;
                }
            } catch(e) { result.jqueryEventsError = e.message; }

            // 原生事件监听器 (Chrome only)
            try {
                result.eventListeners = getEventListeners(el);
                if (result.eventListeners) {
                    const simplified = {};
                    for (const [type, listeners] of Object.entries(result.eventListeners)) {
                        simplified[type] = listeners.map(l => ({
                            type: l.type,
                            useCapture: l.useCapture,
                            listener: l.listener?.toString()?.substring(0, 300)
                        }));
                    }
                    result.eventListeners = simplified;
                }
            } catch(e) { result.eventListenersError = e.message; }

            return result;
        }""")
        print(f"  jQuery data: {jquery_events.get('jqueryData', 'N/A')}")
        print(f"  jQuery data error: {jquery_events.get('jqueryDataError', 'N/A')}")
        print(f"  jQuery events: {jquery_events.get('jqueryEvents', 'N/A')}")
        print(f"  jQuery events error: {jquery_events.get('jqueryEventsError', 'N/A')}")
        print(f"  原生事件监听器: {jquery_events.get('eventListeners', 'N/A')}")
        print(f"  原生事件监听器 error: {jquery_events.get('eventListenersError', 'N/A')}")

        # 7d: 查找JS保存相关函数
        print("\n--- 7d: 查找JS保存相关函数 ---")
        js_functions = page.evaluate("""() => {
            const names = ['save_as_tpl', 'saveWxMsg', 'publishController', 'saveMsg', 'save_wx_msg',
                           'doSave', 'submitSave', 'handleSave', 'saveTemplate', 'saveArticle'];
            const result = {};
            for (const name of names) {
                try {
                    const fn = eval(name);
                    result[name] = {
                        exists: typeof fn === 'function',
                        type: typeof fn,
                        source: typeof fn === 'function' ? fn.toString().substring(0, 500) : String(fn).substring(0, 200)
                    };
                } catch(e) {
                    result[name] = { exists: false, error: e.message };
                }
            }

            // 也检查window上的属性
            const windowKeys = Object.keys(window).filter(k =>
                k.toLowerCase().includes('save') || k.toLowerCase().includes('publish') || k.toLowerCase().includes('sync')
            );
            result._windowSaveKeys = windowKeys;

            return result;
        }""")
        for name, info in js_functions.items():
            if name.startswith('_'):
                print(f"  window中包含save/publish/sync的键: {info}")
            else:
                print(f"  {name}: exists={info.get('exists')}, type={info.get('type')}")
                if info.get('source'):
                    print(f"    source: {info['source'][:200]}")

        # 7e: 对话框内的表单和输入元素
        print("\n--- 7e: 对话框内的表单和输入元素 ---")
        dialog_details = page.evaluate("""() => {
            const dialog = document.querySelector('#save-wx-msg-dialog');
            if (!dialog) return { exists: false };

            const forms = dialog.querySelectorAll('form');
            const inputs = dialog.querySelectorAll('input, select, textarea, button');
            const buttons = dialog.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]');

            return {
                exists: true,
                dialogDisplay: getComputedStyle(dialog).display,
                dialogVisibility: getComputedStyle(dialog).visibility,
                dialogRect: dialog.getBoundingClientRect(),
                formsCount: forms.length,
                forms: Array.from(forms).map(f => ({
                    id: f.id, action: f.action, method: f.method, target: f.target
                })),
                allInputs: Array.from(inputs).map(el => ({
                    tag: el.tagName,
                    type: el.type,
                    name: el.name,
                    id: el.id,
                    value: el.value?.substring(0, 100),
                    checked: el.checked,
                    className: el.className,
                    onclick: el.getAttribute('onclick')
                })),
                allButtons: Array.from(buttons).map(el => ({
                    tag: el.tagName,
                    type: el.type,
                    id: el.id,
                    text: el.textContent?.trim()?.substring(0, 50),
                    className: el.className,
                    onclick: el.getAttribute('onclick'),
                    disabled: el.disabled
                }))
            };
        }""")
        print(f"  对话框存在: {dialog_details.get('exists')}")
        print(f"  对话框显示: display={dialog_details.get('dialogDisplay')}, visibility={dialog_details.get('dialogVisibility')}")
        print(f"  对话框矩形: {dialog_details.get('dialogRect')}")
        print(f"  对话框内表单数: {dialog_details.get('formsCount')}")
        for f in dialog_details.get('forms', []):
            print(f"    表单: id={f['id']}, action={f['action']}, method={f['method']}, target={f['target']}")
        print(f"  对话框内所有输入元素:")
        for inp in dialog_details.get('allInputs', []):
            print(f"    <{inp['tag']} type={inp['type']} name={inp['name']} id={inp['id']} value='{inp['value']}' checked={inp['checked']} onclick={inp['onclick']}>")
        print(f"  对话框内所有按钮:")
        for btn in dialog_details.get('allButtons', []):
            print(f"    <{btn['tag']} type={btn['type']} id={btn['id']} text='{btn['text']}' onclick={btn['onclick']} disabled={btn['disabled']}>")

        # 7f: 搜索保存相关的AJAX请求模式
        print("\n--- 7f: 搜索保存相关的JS代码模式 ---")
        ajax_patterns = page.evaluate("""() => {
            // 检查是否有jQuery AJAX全局处理器
            const result = {};
            try {
                const globalAjax = jQuery.ajaxSettings;
                result.ajaxSettings = { url: globalAjax?.url, type: globalAjax?.type };
            } catch(e) {}

            // 检查XMLHttpRequest拦截
            result.xhrInterceptor = typeof window._originalXHROpen !== 'undefined';

            return result;
        }""")
        print(f"  AJAX模式: {ajax_patterns}")

        # ---- Step 8: 监听网络请求并点击保存按钮 ----
        print("\n" + "=" * 60)
        print("[Step 8] 监听网络请求 + Playwright locator.click() 点击保存按钮")
        print("=" * 60)

        # 收集网络请求
        network_requests = []

        def on_request(request):
            if any(kw in request.url.lower() for kw in ['save', 'publish', 'sync', 'wx', 'template', 'article']):
                network_requests.append({
                    'url': request.url,
                    'method': request.method,
                    'type': 'request'
                })
                print(f"  [网络请求] {request.method} {request.url}")

        def on_response(response):
            if any(kw in response.url.lower() for kw in ['save', 'publish', 'sync', 'wx', 'template', 'article']):
                print(f"  [网络响应] {response.status} {response.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        # 收集控制台消息
        console_messages = []

        def on_console(msg):
            console_messages.append(f"[{msg.type}] {msg.text}")
            if msg.type in ('error', 'warning'):
                print(f"  [控制台 {msg.type}] {msg.text}")

        page.on("console", on_console)

        # 监听dialog事件
        def on_dialog(dialog):
            print(f"  [浏览器对话框] type={dialog.type}, message={dialog.message}")
            dialog.dismiss()

        page.on("dialog", on_dialog)

        # 点击保存按钮
        print("\n  尝试 Playwright locator.click()...")
        try:
            save_submit = page.locator("#save-wx-msg")
            save_submit.click(timeout=5000)
            print("  click() 执行成功")
        except Exception as e:
            print(f"  click() 失败: {e}")
            # 尝试force click
            try:
                save_submit.click(force=True, timeout=5000)
                print("  force click() 执行成功")
            except Exception as e2:
                print(f"  force click() 也失败: {e2}")

        # ---- Step 9: 等待5秒检查结果 ----
        print("\n[Step 9] 等待5秒检查结果...")
        time.sleep(5)
        screenshot(page, "07_after_save_click")

        dialog_state_1 = page.evaluate("""() => {
            const dialog = document.querySelector('#save-wx-msg-dialog');
            if (!dialog) return { exists: false };
            return {
                exists: true,
                display: getComputedStyle(dialog).display,
                visibility: getComputedStyle(dialog).visibility,
                opacity: getComputedStyle(dialog).opacity,
                hasClassHidden: dialog.classList.contains('hidden') || dialog.classList.contains('hide'),
                classList: Array.from(dialog.classList)
            };
        }""")
        print(f"  对话框状态: {dialog_state_1}")

        # 检查是否有错误消息
        error_msgs = page.evaluate("""() => {
            const errors = document.querySelectorAll('.error, .alert, .toast, [class*="error"], [class*="alert"], [class*="toast"], [class*="message"]');
            return Array.from(errors).filter(e => e.offsetParent !== null).map(e => ({
                id: e.id, className: e.className, text: e.textContent?.trim()?.substring(0, 200)
            }));
        }""")
        print(f"  可见错误消息: {error_msgs}")

        print(f"  网络请求数: {len(network_requests)}")
        for req in network_requests:
            print(f"    {req['method']} {req['url']}")

        print(f"  控制台错误/警告:")
        for msg in console_messages:
            if 'error' in msg.lower() or 'warning' in msg.lower():
                print(f"    {msg}")

        # ---- Step 10: 尝试 form.submit() ----
        print("\n" + "=" * 60)
        print("[Step 10] 尝试 form.submit() 作为后备方案")
        print("=" * 60)

        form_submit_result = page.evaluate("""() => {
            const btn = document.querySelector('#save-wx-msg');
            if (!btn) return { error: 'save-wx-msg not found' };

            // 找到按钮所在的表单
            const form = btn.closest('form') || btn.form;
            if (!form) return { error: 'no form found for save button' };

            const formInfo = {
                id: form.id,
                action: form.action,
                method: form.method,
                target: form.target
            };

            try {
                form.submit();
                return { submitted: true, formInfo: formInfo };
            } catch(e) {
                return { submitted: false, error: e.message, formInfo: formInfo };
            }
        }""")
        print(f"  form.submit() 结果: {form_submit_result}")
        screenshot(page, "08_after_form_submit")

        # ---- Step 11: 再等5秒检查 ----
        print("\n[Step 11] 等待5秒再次检查...")
        time.sleep(5)
        screenshot(page, "09_final_check")

        dialog_state_2 = page.evaluate("""() => {
            const dialog = document.querySelector('#save-wx-msg-dialog');
            if (!dialog) return { exists: false };
            return {
                exists: true,
                display: getComputedStyle(dialog).display,
                visibility: getComputedStyle(dialog).visibility,
                opacity: getComputedStyle(dialog).opacity,
                classList: Array.from(dialog.classList)
            };
        }""")
        print(f"  对话框最终状态: {dialog_state_2}")

        # 最终网络请求
        print(f"  最终网络请求数: {len(network_requests)}")
        for req in network_requests:
            print(f"    {req['method']} {req['url']}")

        # 最终控制台消息
        print(f"  最终控制台消息数: {len(console_messages)}")
        for msg in console_messages[-20:]:
            print(f"    {msg}")

        # ---- Step 12: 额外 - 尝试直接调用JS保存函数 ----
        print("\n" + "=" * 60)
        print("[Step 12] 额外调试 - 尝试直接调用JS保存函数")
        print("=" * 60)

        js_save_attempts = page.evaluate("""() => {
            const results = {};

            // 尝试触发按钮的jQuery click
            try {
                results.jqueryClick = 'attempting';
                jQuery('#save-wx-msg').trigger('click');
                results.jqueryClick = 'triggered';
            } catch(e) {
                results.jqueryClick = e.message;
            }

            // 检查是否有iframe中的表单
            try {
                const iframes = document.querySelectorAll('iframe');
                results.iframeCount = iframes.length;
                results.iframeSrcs = Array.from(iframes).map(f => f.src?.substring(0, 100));
            } catch(e) {
                results.iframeError = e.message;
            }

            // 检查是否有遮罩层阻挡
            try {
                const overlays = document.querySelectorAll('.modal-backdrop, .overlay, [class*="mask"], [class*="overlay"]');
                results.overlays = Array.from(overlays).map(o => ({
                    id: o.id, className: o.className,
                    display: getComputedStyle(o).display, zIndex: getComputedStyle(o).zIndex
                }));
            } catch(e) {
                results.overlayError = e.message;
            }

            return results;
        }""")
        print(f"  JS保存尝试结果: {js_save_attempts}")

        time.sleep(3)
        screenshot(page, "10_after_js_save")

        # 最终对话框状态
        final_dialog = page.evaluate("""() => {
            const dialog = document.querySelector('#save-wx-msg-dialog');
            if (!dialog) return { exists: false };
            return {
                display: getComputedStyle(dialog).display,
                visibility: getComputedStyle(dialog).visibility,
                classList: Array.from(dialog.classList)
            };
        }""")
        print(f"  最终对话框状态: {final_dialog}")

        print("\n" + "=" * 60)
        print("调试完成！截图已保存到:", DEBUG_DIR)
        print("=" * 60)

        # 保持浏览器打开以便手动检查
        input("\n按回车键关闭浏览器...")
        context.close()


if __name__ == "__main__":
    main()
