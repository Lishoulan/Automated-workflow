import sys, time, json, re, base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("wechat_articledata_cover")

settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

from playwright.sync_api import sync_playwright

profile_dir = settings.account_runtime_dir / "wechat_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

prepare_browser_profile(
    profile_dir=profile_dir,
    project_root=settings.project_root,
    logger=logger,
    label="wechat:articledata",
    extra_paths=[settings.account_runtime_dir],
)

cover_path = r"F:\Automated-workflow-main\output\runs\20260531-145155_五道口-跑出116个独角兽\publish\images\cover.png"
if not Path(cover_path).exists():
    cover_path = str(Path(__file__).resolve().parent.parent / "output" / "test_cover.png")

TEST_TITLE = "ArticleData封面V42"

def dismiss_dialogs(page):
    page.evaluate("""() => {
        const keywords = ['切换账号', '实名', '退出登录', '插件', '安全隐患'];
        document.querySelectorAll('.weui-desktop-dialog').forEach(dialog => {
            const text = (dialog.textContent || '').trim();
            if (keywords.some(kw => text.includes(kw))) {
                for (const t of ['我知道了', '取消', '关闭']) {
                    const btns = dialog.querySelectorAll('button, a, [role="button"]');
                    for (const btn of btns) {
                        if ((btn.textContent || '').trim() === t) { btn.click(); return; }
                    }
                }
                const closeBtn = dialog.querySelector('.weui-desktop-dialog__close-btn');
                if (closeBtn) closeBtn.click();
            }
        });
        document.querySelectorAll('.weui-desktop-dialog__wrp').forEach(wrp => {
            const dialog = wrp.querySelector('.weui-desktop-dialog');
            if (dialog) {
                const text = (dialog.textContent || '').trim();
                if (keywords.some(kw => text.includes(kw))) wrp.style.display = 'none';
            }
        });
        document.querySelectorAll('.weui-desktop-mask').forEach(m => m.style.display = 'none');
        document.querySelectorAll('.detected_dialog_wrapper').forEach(w => {
            w.style.display = 'none';
        });
    }""")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="msedge",
    )
    try:
        page = browser.pages[0] if browser.pages else browser.new_page()
        base = settings.wechat.base_url.rstrip("/")

        logger.info("Step 1: Open WeChat backend")
        page.goto(base, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        current_url = page.url
        token_match = re.search(r"token=(\d+)", current_url)
        token = token_match.group(1) if token_match else ""

        for _ in range(5):
            dismiss_dialogs(page)
            page.wait_for_timeout(500)

        logger.info("Step 2: Open new article editor")
        new_article_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&token={token}"
        page.goto(new_article_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        for _ in range(5):
            dismiss_dialogs(page)
            page.wait_for_timeout(500)

        logger.info("Step 3: Fill title and content")
        page.evaluate("""(title) => {
            const el = document.querySelector('#title');
            if (el) {
                el.value = title;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""", TEST_TITLE)
        page.wait_for_timeout(500)

        with open(cover_path, "rb") as f:
            cover_data = f.read()
        cover_b64 = base64.b64encode(cover_data).decode("utf-8")

        inline_upload = page.evaluate("""async (args) => {
            const [b64Data, token] = args;
            const binaryStr = atob(b64Data);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            const blob = new Blob([bytes], {type: 'image/png'});
            const file = new File([blob], 'cover.png', {type: 'image/png'});
            const formData = new FormData();
            formData.append('file', file, 'cover.png');
            const url = '/cgi-bin/filetransfer?action=upload_material&scene=2&writetype=doublewrite&token=' + token;
            const response = await fetch(url, {method: 'POST', body: formData});
            return await response.text();
        }""", [cover_b64, token])
        inline_data = json.loads(inline_upload)
        inline_media_id = str(inline_data.get("content", ""))
        inline_cdn_url = inline_data.get("cdn_url", "").replace("\\/", "/")
        logger.info("Inline image: media_id=%s", inline_media_id)

        cover_upload = page.evaluate("""async (args) => {
            const [b64Data, token] = args;
            const binaryStr = atob(b64Data);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            const blob = new Blob([bytes], {type: 'image/png'});
            const file = new File([blob], 'cover.png', {type: 'image/png'});
            const formData = new FormData();
            formData.append('file', file, 'cover.png');
            const url = '/cgi-bin/filetransfer?action=upload_material&scene=1&writetype=doublewrite&token=' + token;
            const response = await fetch(url, {method: 'POST', body: formData});
            return await response.text();
        }""", [cover_b64, token])
        cover_data_resp = json.loads(cover_upload)
        cover_media_id = str(cover_data_resp.get("content", ""))
        cover_cdn_url = cover_data_resp.get("cdn_url", "").replace("\\/", "/")
        logger.info("Cover image (scene=1): media_id=%s, cdn_url=%s", cover_media_id, cover_cdn_url[:80])

        article_html = (
            '<p>这是一篇测试文章。</p>'
            f'<p><img src="{inline_cdn_url}" data-src="{inline_cdn_url}" data-fileid="{inline_media_id}" data-type="png" class="rich_pages wxw-img" data-ratio="0.4256" data-w="900" style="width: 100%;"/></p>'
            '<p>以上是正文图片。</p>'
        )

        page.evaluate("""() => {
            const overlays = document.querySelectorAll('.weui-desktop-dialog__wrp, .weui-desktop-mask, .detected_dialog_wrapper');
            overlays.forEach(el => el.style.display = 'none');
        }""")

        pm_locator = page.locator('.ProseMirror').last
        pm_locator.click(force=True, timeout=3000)
        page.wait_for_timeout(500)
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(300)

        page.evaluate("""(html) => {
            const pmElements = document.querySelectorAll('.ProseMirror');
            const pmEl = pmElements[pmElements.length - 1];
            if (!pmEl) return;
            pmEl.focus();
            const clipboardData = new DataTransfer();
            clipboardData.setData('text/html', html);
            clipboardData.setData('text/plain', '测试');
            const pasteEvent = new ClipboardEvent('paste', {
                bubbles: true,
                cancelable: true,
                clipboardData: clipboardData,
            });
            pmEl.dispatchEvent(pasteEvent);
        }""", article_html)
        page.wait_for_timeout(5000)

        logger.info("Step 4: Directly set articleData cover fields via Vue $set")
        dismiss_dialogs(page)
        page.wait_for_timeout(500)

        set_result = page.evaluate("""(args) => {
            const [coverFileid, coverCdnUrl] = args;
            const vueApp = document.querySelector('#vue_app')?.__vue__;
            if (!vueApp) return { noVue: true };

            const ad = vueApp.coverDialog?.articleData;
            if (!ad) return { noArticleData: true };

            vueApp.$set(ad, 'file_id', coverFileid);
            vueApp.$set(ad, 'cdn_url', coverCdnUrl);
            vueApp.$set(ad, 'cdn_235_1_url', coverCdnUrl);
            vueApp.$set(ad, 'cdn_1_1_url', coverCdnUrl);
            vueApp.$set(ad, 'show_cover_pic', 1);
            vueApp.$set(ad, 'last_choose_cover_from', 1);
            vueApp.$set(ad, 'app_cover_auto', 0);
            vueApp.$set(ad, 'crop_list', {
                crop_list: [
                    {crop_type: 1, x: 0, y: 0, w: 900, h: 383},
                    {crop_type: 2, x: 258, y: 0, w: 383, h: 383}
                ]
            });

            vueApp.$set(vueApp.coverDialog, 'fileid', coverFileid);
            vueApp.$set(vueApp.coverDialog, 'cdn_url', coverCdnUrl);
            vueApp.$set(vueApp.coverDialog, 'isShow', false);

            const coverArea = document.querySelector('.js_cover_btn_area');
            if (coverArea) {
                const addIcon = coverArea.querySelector('.add_cover');
                const noneImageSpan = coverArea.querySelector('.js_share_type_none_image');
                const imageSpan = coverArea.querySelector('.js_share_type_image');
                if (addIcon) addIcon.style.display = 'none';
                if (noneImageSpan) noneImageSpan.style.display = 'none';
                if (imageSpan) {
                    imageSpan.style.display = 'block';
                    let img = imageSpan.querySelector('img');
                    if (!img) {
                        img = document.createElement('img');
                        imageSpan.insertBefore(img, imageSpan.firstChild);
                    }
                    img.src = coverCdnUrl;
                    img.style.width = '100%';
                    img.style.height = '100%';
                    img.style.objectFit = 'cover';
                }
            }

            vueApp.$forceUpdate();

            return {
                ad_file_id: ad.file_id,
                ad_cdn_url: ad.cdn_url?.substring(0, 100),
                ad_cdn_235_1_url: ad.cdn_235_1_url?.substring(0, 100),
                ad_cdn_1_1_url: ad.cdn_1_1_url?.substring(0, 100),
                ad_show_cover_pic: ad.show_cover_pic,
                ad_crop_list: ad.crop_list,
                hasImage: !!coverArea?.querySelector('img'),
            };
        }""", [cover_media_id, cover_cdn_url])
        logger.info("Set result: %s", json.dumps(set_result, ensure_ascii=False, indent=2))

        if set_result.get("hasImage"):
            logger.info("=== COVER IMAGE SET IN UI! ===")

        logger.info("Step 5: Now click '发表' button to trigger the editor's save+publish flow")
        captured_requests = []

        def capture_route(route):
            req = route.request
            url = req.url
            if "operate_appmsg" in url or "appmsgpublish" in url:
                try:
                    pd = req.post_data
                    captured_requests.append({
                        "url": url[:300],
                        "method": req.method,
                        "post_data": pd[:5000] if pd else "",
                    })
                except:
                    pass
            route.continue_()

        page.route("**/*", capture_route)

        try:
            publish_btn = page.locator('#js_submit')
            if publish_btn.is_visible(timeout=3000):
                publish_btn.click(timeout=5000)
                logger.info("Clicked publish button!")
                page.wait_for_timeout(5000)

                logger.info("Captured %d requests", len(captured_requests))
                for req in captured_requests:
                    logger.info("  %s %s", req["method"], req["url"][:150])
                    if req.get("post_data"):
                        pd = req["post_data"]
                        if "fileid0" in pd or "file_id" in pd:
                            logger.info("  === COVER FILEID FOUND IN REQUEST! ===")
                        logger.info("  Data (first 2000): %s", pd[:2000])

                for attempt in range(20):
                    dialog_check = page.evaluate("""() => {
                        const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                        const visible = [];
                        for (const d of dialogs) {
                            const wrp = d.closest('.weui-desktop-dialog__wrp');
                            const isVisible = wrp ? window.getComputedStyle(wrp).display !== 'none' : true;
                            if (isVisible) visible.push(d.textContent?.trim()?.substring(0, 200));
                        }
                        return visible;
                    }""")

                    if not dialog_check:
                        current = page.url
                        logger.info("No dialogs, URL: %s", current[:100])
                        if "appmsgpublish" in current or "sub=list" in current:
                            logger.info("=== PUBLISH SUCCESSFUL! ===")
                            break
                        break

                    logger.info("Attempt %d: %s", attempt, json.dumps(dialog_check, ensure_ascii=False)[:300])
                    dismiss_dialogs(page)
                    page.wait_for_timeout(500)

                    try:
                        confirm = page.locator('.weui-desktop-dialog button.weui-desktop-btn_primary').first
                        if confirm.is_visible(timeout=2000):
                            btn_text = confirm.text_content(timeout=1000).strip()
                            logger.info("Primary button: %s", btn_text)
                            confirm.click(timeout=5000)
                            page.wait_for_timeout(5000)
                    except:
                        pass

                    current_url = page.url
                    if "appmsgpublish" in current_url or "sub=list" in current_url:
                        logger.info("=== PUBLISH SUCCESSFUL! ===")
                        break
                    page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning("Publish failed: %s", e)

        page.unroute("**/*")

        page.screenshot(path=str(settings.output_dir / "wechat_articledata_cover.png"), full_page=True)
        logger.info("Test completed.")

    finally:
        browser.close()
