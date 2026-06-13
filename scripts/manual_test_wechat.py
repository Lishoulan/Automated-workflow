import sys, time, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("wechat_manual")

settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

from playwright.sync_api import sync_playwright

profile_dir = settings.account_runtime_dir / "wechat_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

prepare_browser_profile(
    profile_dir=profile_dir,
    project_root=settings.project_root,
    logger=logger,
    label="wechat:manual",
    extra_paths=[settings.account_runtime_dir],
)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="msedge",
    )
    try:
        page = browser.pages[0] if browser.pages else browser.new_page()
        base = settings.wechat.base_url.rstrip("/")
        page.goto(base, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        current_url = page.url
        token_match = re.search(r"token=(\d+)", current_url)
        token = token_match.group(1) if token_match else ""
        if not token:
            page.wait_for_timeout(3000)
            current_url = page.url
            token_match = re.search(r"token=(\d+)", current_url)
            token = token_match.group(1) if token_match else ""

        new_article_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&token={token}"
        page.goto(new_article_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(5000)

        print("\n" + "=" * 60)
        print("请在浏览器中手动完成以下操作：")
        print("1. 关闭所有弹出的对话框（切换账号、实名、插件等）")
        print("2. 确认编辑器页面没有遮挡的对话框")
        print("=" * 60 + "\n")

        logger.info("Waiting 60 seconds for user to close dialogs manually...")
        page.wait_for_timeout(60000)
        logger.info("Continuing after manual dialog close.")

        logger.info("User confirmed dialogs are closed.")

        visible_dialogs = page.evaluate("""() => {
            const dialogs = document.querySelectorAll('.weui-desktop-dialog');
            const visible = [];
            dialogs.forEach(d => {
                const wrp = d.closest('.weui-desktop-dialog__wrp');
                const style = wrp ? window.getComputedStyle(wrp) : window.getComputedStyle(d);
                if (style.display !== 'none') {
                    visible.push(d.textContent?.trim()?.substring(0, 100));
                }
            });
            return visible;
        }""")
        logger.info("Visible dialogs after manual close: %d - %s", len(visible_dialogs), visible_dialogs)

        title = "测试封面上传-手动关闭对话框"
        page.evaluate("""(title) => {
            const el = document.querySelector('#title');
            if (el) {
                el.value = title;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""", title)
        logger.info("Filled title")

        test_html = "<p>测试内容。</p>"
        page.evaluate("""(html) => {
            const el = document.querySelector('.mock-iframe-body');
            if (el) {
                el.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertHTML', false, html);
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }
        }""", test_html)
        page.wait_for_timeout(2000)

        cover_path = r"F:\Automated-workflow-main\output\runs\20260531-145155_五道口-跑出116个独角兽\publish\images\cover.png"

        logger.info("Step 1: Click cover button")
        page.evaluate("""() => {
            const coverBtn = document.querySelector('.js_cover_btn_area');
            if (coverBtn) coverBtn.click();
        }""")
        page.wait_for_timeout(1500)

        logger.info("Step 2: Click 'from image library'")
        page.evaluate("""() => {
            const link = document.querySelector('.js_imagedialog');
            if (link) link.click();
        }""")
        page.wait_for_timeout(3000)

        has_image_dialog = page.evaluate("""() => {
            const dialogs = document.querySelectorAll('.weui-desktop-dialog');
            for (const d of dialogs) {
                const wrp = d.closest('.weui-desktop-dialog__wrp');
                const style = wrp ? window.getComputedStyle(wrp) : window.getComputedStyle(d);
                if (style.display !== 'none' && d.textContent.includes('选择图片')) {
                    return true;
                }
            }
            return false;
        }""")
        logger.info("Image library dialog appeared: %s", has_image_dialog)

        if has_image_dialog:
            logger.info("Step 3: Click upload button and set file")
            with page.expect_file_chooser(timeout=10000) as fc_info:
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('a, button, span');
                    for (const btn of btns) {
                        const text = (btn.textContent || '').trim();
                        if (text === '上传文件' || text === '上传图片' || text === '本地上传') {
                            const style = window.getComputedStyle(btn);
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && style.display !== 'none') {
                                btn.click();
                                return;
                            }
                        }
                    }
                }""")
            file_chooser = fc_info.value
            file_chooser.set_files(cover_path)
            logger.info("File set via file chooser!")
            page.wait_for_timeout(5000)

            page.evaluate("""() => {
                const images = document.querySelectorAll('.img_item, .image_item, .pic_item, [class*="img_item"]');
                for (const img of images) {
                    if (!img.classList.contains('selected')) {
                        img.click();
                        return;
                    }
                }
            }""")
            page.wait_for_timeout(1000)

            page.evaluate("""() => {
                const btns = document.querySelectorAll('button, a.btn');
                for (const btn of btns) {
                    if (btn.textContent.trim() === '确定') {
                        btn.click();
                        return;
                    }
                }
            }""")
            page.wait_for_timeout(3000)

            has_cover = page.evaluate("""() => {
                const coverArea = document.querySelector('.js_cover_btn_area');
                return !!coverArea?.querySelector('img');
            }""")
            logger.info("Cover image set: %s", has_cover)

            if has_cover:
                logger.info("SUCCESS! Cover image uploaded and set!")

                page.evaluate("""() => {
                    const btn = document.querySelector('#js_submit button');
                    if (btn) btn.click();
                }""")
                page.wait_for_timeout(5000)

                logger.info("Clicking publish button...")
                page.evaluate("""() => {
                    const btn = document.querySelector('#js_send button.mass_send');
                    if (btn) btn.click();
                }""")

                for i in range(30):
                    page.wait_for_timeout(2000)
                    state = page.evaluate("""() => {
                        const jsSend = document.querySelector('#js_send');
                        return {
                            isLoading: jsSend ? jsSend.classList.contains('btn_loading') : false,
                            url: window.location.href.substring(0, 120),
                        };
                    }""")
                    logger.info("[%ds] loading=%s url=...%s", (i+1)*2, state["isLoading"], state["url"][-50:])
                    if state["isLoading"]:
                        logger.info("Publish button is loading!")
                    if not state["isLoading"] and "appmsg_edit" not in state["url"]:
                        logger.info("Page navigated away from editor!")
                        break
                    if "appmsgpublish" in state["url"] and "sub=list" in state["url"]:
                        logger.info("Publish success detected via URL!")
                        break

        page.screenshot(path=str(settings.output_dir / "wechat_manual_test.png"), full_page=True)

    finally:
        browser.close()
