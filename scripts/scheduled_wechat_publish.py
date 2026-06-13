import sys
import json
import re
import base64
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.pipeline import ContentFactoryPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            str(Path(__file__).resolve().parent.parent / "output" / "wechat_scheduled_publish.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("wechat_scheduled")


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
                const dismissKw = ['切换账号', '实名', '退出登录', '插件', '安全隐患'];
                if (dismissKw.some(kw => text.includes(kw))) wrp.style.display = 'none';
            }
        });
        document.querySelectorAll('.detected_dialog_wrapper').forEach(w => {
            w.style.display = 'none';
        });
    }""")


def upload_cover(page, token, img_b64, scene=1):
    return page.evaluate("""async (args) => {
        const [token, imgB64, scene] = args;
        try {
            const binaryStr = atob(imgB64);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            const blob = new Blob([bytes], {type: 'image/png'});
            const formData = new FormData();
            formData.append('file', blob, 'cover.png');
            const resp = await fetch('/cgi-bin/filetransfer?action=upload_material&scene=' + scene + '&f=json&token=' + token, {
                method: 'POST', body: formData,
            });
            return await resp.json();
        } catch(e) { return {error: e.message}; }
    }""", [token, img_b64, scene])


def get_cdn_url_via_content_upload(page, token, img_b64):
    return page.evaluate("""async (args) => {
        const [token, imgB64] = args;
        try {
            const binaryStr = atob(imgB64);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            const blob = new Blob([bytes], {type: 'image/png'});
            const formData = new FormData();
            formData.append('file', blob, 'cover.png');
            const resp = await fetch('/cgi-bin/filetransfer?action=upload_material&scene=2&f=json&token=' + token, {
                method: 'POST', body: formData,
            });
            const data = await resp.json();
            return data;
        } catch(e) { return {error: e.message}; }
    }""", [token, img_b64])


def generate_cover_images(output_dir, timestamp):
    from PIL import Image, ImageDraw, ImageFont
    import platform as _platform

    cover_wide_path = output_dir / f"sched_cover_wide_{timestamp}.png"
    img = Image.new("RGB", (900, 383), color=(15, 76, 129))
    draw = ImageDraw.Draw(img)

    # 跨平台字体候选列表
    system = _platform.system()
    font_candidates = []
    if system == "Windows":
        font_candidates = ["msyh.ttc", "C:/Windows/Fonts/msyh.ttc"]
    elif system == "Darwin":
        font_candidates = [
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
    else:
        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ]

    font = None
    font_small = None
    for fc in font_candidates:
        try:
            font = ImageFont.truetype(fc, 36)
            font_small = ImageFont.truetype(fc, 20)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
        font_small = font
    draw.text((450, 160), "科技产业观察", fill="white", font=font, anchor="mm")
    draw.text((450, 210), datetime.now().strftime("%Y-%m-%d %H:%M"), fill=(200, 220, 240), font=font_small, anchor="mm")
    img.save(str(cover_wide_path))

    cover_sq_path = output_dir / f"sched_cover_sq_{timestamp}.png"
    sq_img = Image.new("RGB", (900, 900), color=(15, 76, 129))
    sq_draw = ImageDraw.Draw(sq_img)
    sq_draw.text((450, 420), "科技产业", fill="white", font=font, anchor="mm")
    sq_draw.text((450, 470), "观察", fill="white", font=font, anchor="mm")
    sq_img.save(str(cover_sq_path))

    return cover_wide_path, cover_sq_path


def publish_wechat_article(title: str, content: str, settings) -> dict:
    from playwright.sync_api import sync_playwright

    profile_dir = settings.account_runtime_dir / "wechat_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label="wechat:scheduled",
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

            logger.info("Step 1: Opening WeChat backend...")
            page.goto(base, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            token_match = re.search(r"token=(\d+)", page.url)
            token = token_match.group(1) if token_match else ""
            logger.info("Token: %s", token[:6] + "..." if token else "EMPTY")

            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            logger.info("Step 2: Uploading cover images...")
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            cover_wide_path, cover_sq_path = generate_cover_images(settings.output_dir, timestamp)

            with open(str(cover_wide_path), "rb") as f:
                wide_b64 = base64.b64encode(f.read()).decode("ascii")
            with open(str(cover_sq_path), "rb") as f:
                sq_b64 = base64.b64encode(f.read()).decode("ascii")

            wide_result = upload_cover(page, token, wide_b64, scene=1)
            wide_file_id = str(wide_result.get("content", "")) if wide_result.get("base_resp", {}).get("ret") == 0 else ""
            logger.info("Wide cover file_id: %s", wide_file_id)

            sq_result = upload_cover(page, token, sq_b64, scene=1)
            sq_file_id = str(sq_result.get("content", "")) if sq_result.get("base_resp", {}).get("ret") == 0 else ""
            logger.info("Square cover file_id: %s", sq_file_id)

            if not wide_file_id:
                return {"status": "error", "message": "Wide cover upload failed"}

            cover_cdn_url = ""

            logger.info("Step 3: Opening editor...")
            new_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&token={token}&lang=zh_CN"
            page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)
            for _ in range(15):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            logger.info("Step 4: Filling title via JS nativeInputValueSetter...")
            page.evaluate("""(t) => {
                const el = document.querySelector('#title');
                if (el) {
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                    nativeInputValueSetter.call(el, t);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""", title)
            page.wait_for_timeout(500)
            title_val = page.evaluate("() => document.querySelector('#title').value")
            logger.info("Title filled: '%s'", title_val[:50])

            logger.info("Step 5: Saving draft via #js_submit (create)...")
            captured_appmsg_responses = []

            def capture_appmsg_resp(response):
                if "operate_appmsg" in response.url:
                    try:
                        body = response.text()
                        captured_appmsg_responses.append({"url": response.url[:300], "status": response.status, "body": body[:3000]})
                        logger.info("CAPTURED: [%d] %s", response.status, response.url.split("?")[0].split("/")[-1])
                    except:
                        pass

            page.on("response", capture_appmsg_resp)

            try:
                page.locator("#js_submit button").click(timeout=10000)
                logger.info("Clicked #js_submit via Playwright")
            except Exception as e:
                logger.warning("Playwright click failed: %s, trying JS click...", e)
                page.evaluate("""() => {
                    const btn = document.querySelector('#js_submit button');
                    if (btn) btn.click();
                }""")
                logger.info("Clicked #js_submit via JS fallback")

            page.wait_for_timeout(8000)
            page.remove_listener("response", capture_appmsg_resp)

            app_msg_id = ""

            url_match = re.search(r"appmsgid=(\d+)", page.url)
            if url_match:
                app_msg_id = url_match.group(1)

            if not app_msg_id:
                for r in captured_appmsg_responses:
                    try:
                        d = json.loads(r.get("body", ""))
                        aid = str(d.get("appMsgId", d.get("appmsgid", ""))).strip()
                        if aid:
                            app_msg_id = aid
                            break
                    except:
                        pass

            if not app_msg_id:
                app_msg_id = page.evaluate("""() => {
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        if (el.__vue__) {
                            const d = el.__vue__.$data || {};
                            if (d.appMsgId) return String(d.appMsgId);
                        }
                    }
                    return '';
                }""")

            logger.info("Draft AppMsgId: %s", app_msg_id)

            if not app_msg_id:
                return {"status": "error", "message": "No AppMsgId after draft create"}

            logger.info("Step 6: Updating draft content via operate_appmsg sub=update...")
            update_result = page.evaluate("""async (args) => {
                const [token, appMsgId, title, content, fileId, sqFileId, cdnUrl] = args;
                const formData = new FormData();
                formData.append('token', token);
                formData.append('lang', 'zh_CN');
                formData.append('f', 'json');
                formData.append('ajax', '1');
                formData.append('fingerprint', '');
                formData.append('random', String(Math.random()));
                formData.append('sub', 'update');
                formData.append('type', '77');
                formData.append('id', appMsgId);
                formData.append('AppMsgId', appMsgId);
                formData.append('count', '1');
                formData.append('data_seq', '0');
                formData.append('operate_from', 'Chrome');
                formData.append('title0', title);
                formData.append('content0', content);
                formData.append('author0', '');
                formData.append('fileid0', fileId);
                formData.append('sq_fileid0', sqFileId);
                formData.append('digest0', '');
                formData.append('sourceurl0', '');
                formData.append('need_open_comment0', '0');
                formData.append('music_id0', '');
                formData.append('video_id0', '');
                formData.append('voteid0', '');
                formData.append('voteismulti0', '');
                formData.append('voteid20', '');
                formData.append('show_cover_pic0', '1');
                formData.append('shortvideofileid0', '');
                formData.append('copyright_type0', '0');
                formData.append('releasefirst0', '');
                formData.append('pay_fee0', '0');
                formData.append('fee_type0', '');
                formData.append('pay_album_info0', '');
                formData.append('appmsg_album_info0', '');
                formData.append('ori_white_list0', '');
                formData.append('free_content_length0', '');
                formData.append('can_reward0', '0');
                formData.append('related_video0', '');
                formData.append('is_set_sync_to_finder0', '0');
                formData.append('mediaapi_publish_status0', '0');
                formData.append('cdns0', '');
                formData.append('cdn_235_1_url0', cdnUrl);
                formData.append('cdn_1_1_url0', cdnUrl);
                formData.append('cdn_url0', cdnUrl);
                formData.append('cover_check_info', JSON.stringify({err_format: '', err_idx: 0}));
                formData.append('save_type', '1');
                try {
                    const resp = await fetch('/cgi-bin/operate_appmsg?t=ajax-response&sub=update&token=' + token, {
                        method: 'POST', body: formData,
                    });
                    return await resp.text();
                } catch(e) { return 'ERROR: ' + e.message; }
            }""", [token, str(app_msg_id), title, content, str(wide_file_id), str(sq_file_id), cover_cdn_url])
            logger.info("Update result: %s", update_result[:300])

            update_ret = None
            try:
                update_data = json.loads(update_result)
                update_ret = update_data.get("ret", update_data.get("base_resp", {}).get("ret"))
            except:
                pass

            if update_ret == 0 or update_ret == "0":
                logger.info("Content update SUCCESS!")
            else:
                logger.warning("Content update may have failed, ret=%s", update_ret)

            logger.info("Step 7: Reloading editor with updated draft...")
            edit_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&appmsgid={app_msg_id}&token={token}&lang=zh_CN"
            page.goto(edit_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            editor_state = page.evaluate("""() => {
                const editors = document.querySelectorAll('.ProseMirror');
                const titleValue = document.querySelector('#title') ? document.querySelector('#title').value : '';
                const bodyText = editors.length > 1 ? editors[1].textContent.trim().substring(0, 200) : '';
                const jsSend = document.querySelector('#js_send');
                const jsSendVisible = jsSend ? jsSend.offsetParent !== null : false;
                return {titleValue, bodyText, jsSendVisible};
            }""")
            logger.info("Editor state: title='%s' body='%s' jsSend=%s",
                        editor_state.get("titleValue", "")[:50],
                        editor_state.get("bodyText", "")[:100],
                        editor_state.get("jsSendVisible", False))

            if not editor_state.get("bodyText", "") or "从这里开始写正文" in editor_state.get("bodyText", ""):
                logger.warning("Body content appears empty after update!")

            logger.info("Step 8: Getting CDN URL from Vue articleData (auto-populated after reload)...")
            vue_cover_data = page.evaluate("""() => {
                const allEls = document.querySelectorAll('*');
                for (const el of allEls) {
                    if (el.__vue__) {
                        const d = el.__vue__.$data || {};
                        if (d.articleData) {
                            return {
                                file_id: d.articleData.file_id || '',
                                cdn_url: d.articleData.cdn_url || '',
                                cdn_235_1_url: d.articleData.cdn_235_1_url || '',
                                cdn_1_1_url: d.articleData.cdn_1_1_url || '',
                                cover: d.articleData.cover || '',
                            };
                        }
                    }
                }
                return {};
            }""")
            cover_cdn_url = vue_cover_data.get("cdn_url", "") or vue_cover_data.get("cdn_235_1_url", "")
            cover_cdn_1_1_url = vue_cover_data.get("cdn_1_1_url", "")
            logger.info("Vue cover data: cdn_url=%s cdn_1_1_url=%s", 
                        cover_cdn_url[:80] if cover_cdn_url else "EMPTY",
                        cover_cdn_1_1_url[:80] if cover_cdn_1_1_url else "EMPTY")

            if not cover_cdn_1_1_url and cover_cdn_url:
                logger.info("cdn_1_1_url is empty, saving draft again to trigger cover crop...")
                page.locator("#js_submit button").click(timeout=10000)
                page.wait_for_timeout(8000)

                logger.info("Reloading editor after second save...")
                page.goto(edit_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                for _ in range(10):
                    dismiss_dialogs(page)
                    page.wait_for_timeout(300)

                vue_cover_data2 = page.evaluate("""() => {
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        if (el.__vue__) {
                            const d = el.__vue__.$data || {};
                            if (d.articleData) {
                                return {
                                    cdn_url: d.articleData.cdn_url || '',
                                    cdn_235_1_url: d.articleData.cdn_235_1_url || '',
                                    cdn_1_1_url: d.articleData.cdn_1_1_url || '',
                                };
                            }
                        }
                    }
                    return {};
                }""")
                cover_cdn_1_1_url = vue_cover_data2.get("cdn_1_1_url", "")
                if not cover_cdn_url:
                    cover_cdn_url = vue_cover_data2.get("cdn_url", "") or vue_cover_data2.get("cdn_235_1_url", "")
                logger.info("After second save: cdn_1_1_url=%s", cover_cdn_1_1_url[:80] if cover_cdn_1_1_url else "EMPTY")

            if not cover_cdn_url:
                cover_cdn_url = page.evaluate("""() => {
                    const allImgs = document.querySelectorAll('img[src*="mmbiz.qpic.cn"]');
                    for (const img of allImgs) {
                        if (img.src && img.src.startsWith('http')) return img.src;
                    }
                    return '';
                }""")
                logger.info("CDN URL from editor images: %s", cover_cdn_url[:100] if cover_cdn_url else "EMPTY")

            if not cover_cdn_url:
                logger.warning("No CDN URL found after reload, will attempt publish with file_id only")

            logger.info("Step 9: Setting Vue articleData cover fields...")
            set_result = page.evaluate("""(args) => {
                const [fileId, cdnUrl, cdn11Url] = args;
                const allEls = document.querySelectorAll('*');
                for (const el of allEls) {
                    if (el.__vue__) {
                        const d = el.__vue__.$data || {};
                        if (d.articleData) {
                            el.__vue__.$set(d.articleData, 'file_id', fileId);
                            if (cdnUrl) {
                                el.__vue__.$set(d.articleData, 'cdn_url', cdnUrl);
                                el.__vue__.$set(d.articleData, 'cdn_235_1_url', cdnUrl);
                            }
                            if (cdn11Url) {
                                el.__vue__.$set(d.articleData, 'cdn_1_1_url', cdn11Url);
                            } else if (cdnUrl) {
                                el.__vue__.$set(d.articleData, 'cdn_1_1_url', cdnUrl);
                            }
                            el.__vue__.$set(d.articleData, 'show_cover_pic', 1);
                            return 'set OK with cdn=' + (cdnUrl ? 'yes' : 'no') + ' cdn11=' + (cdn11Url ? 'yes' : 'no');
                        }
                    }
                }
                return 'no articleData found';
            }""", [wide_file_id, cover_cdn_url, cover_cdn_1_1_url])
            logger.info("Vue set: %s", set_result)
            page.wait_for_timeout(2000)

            js_send_visible = page.evaluate("""() => {
                const btn = document.querySelector('#js_send');
                return btn ? btn.offsetParent !== null : false;
            }""")
            logger.info("#js_send visible: %s", js_send_visible)

            if not js_send_visible:
                logger.warning("#js_send not visible, waiting and retrying...")
                page.wait_for_timeout(5000)
                js_send_visible = page.evaluate("""() => {
                    const btn = document.querySelector('#js_send');
                    return btn ? btn.offsetParent !== null : false;
                }""")

            if not js_send_visible:
                logger.error("#js_send still not visible after waiting")
                return {"status": "error", "message": "#js_send not visible", "app_msg_id": app_msg_id}

            logger.info("Step 10: Publishing via #js_send dispatchEvent...")
            masssend_data = []

            def on_resp(response):
                url = response.url
                if any(kw in url for kw in ["masssend", "appmsgpublish", "freepublish"]):
                    try:
                        body = response.text()
                        masssend_data.append({"status": response.status, "url": url[:300], "body": body[:2000]})
                        logger.info("RESP [%d]: %s body=%s", response.status, url.split("?")[0].split("/")[-1], body[:300])
                    except:
                        pass

            page.on("response", on_resp)

            logger.info("Clicking #js_send via dispatchEvent(MouseEvent)...")
            page.evaluate("""() => {
                const btn = document.querySelector('#js_send');
                if (btn) {
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                }
            }""")
            page.wait_for_timeout(5000)

            first_dialog = page.evaluate("""() => {
                const allDialogs = document.querySelectorAll('.weui-desktop-dialog__wrp');
                for (const d of allDialogs) {
                    const style = window.getComputedStyle(d);
                    if (style.display !== 'none') {
                        const dialog = d.querySelector('.weui-desktop-dialog');
                        const btns = dialog ? dialog.querySelectorAll('button') : [];
                        const btnTexts = [];
                        btns.forEach(b => { const t = (b.textContent || '').trim(); if (t) btnTexts.push(t); });
                        if (btnTexts.includes('发表') && btnTexts.includes('取消')) return true;
                    }
                }
                return false;
            }""")

            if not first_dialog:
                page.wait_for_timeout(5000)
                first_dialog = page.evaluate("""() => {
                    const allDialogs = document.querySelectorAll('.weui-desktop-dialog__wrp');
                    for (const d of allDialogs) {
                        const style = window.getComputedStyle(d);
                        if (style.display !== 'none') {
                            const dialog = d.querySelector('.weui-desktop-dialog');
                            const btns = dialog ? dialog.querySelectorAll('button') : [];
                            const btnTexts = [];
                            btns.forEach(b => { const t = (b.textContent || '').trim(); if (t) btnTexts.push(t); });
                            if (btnTexts.includes('发表') && btnTexts.includes('取消')) return true;
                        }
                    }
                    return false;
                }""")

            if not first_dialog:
                page.remove_listener("response", on_resp)
                logger.error("No publish dialog. Captured responses: %s", json.dumps(masssend_data, ensure_ascii=False)[:500])
                return {"status": "error", "message": "No publish dialog after #js_send click", "masssend_data": masssend_data}

            page.locator('.weui-desktop-dialog__wrp .weui-desktop-dialog button:has-text("发表")').first.click(timeout=10000)
            logger.info("Clicked '发表'")
            page.wait_for_timeout(5000)

            second_dialog = page.evaluate("""() => {
                const allDialogs = document.querySelectorAll('.weui-desktop-dialog__wrp');
                for (const d of allDialogs) {
                    const style = window.getComputedStyle(d);
                    if (style.display !== 'none') {
                        const dialog = d.querySelector('.weui-desktop-dialog');
                        const btns = dialog ? dialog.querySelectorAll('button') : [];
                        const btnTexts = [];
                        btns.forEach(b => { const t = (b.textContent || '').trim(); if (t) btnTexts.push(t); });
                        if (btnTexts.includes('继续发表')) return true;
                    }
                }
                return false;
            }""")

            if not second_dialog:
                page.remove_listener("response", on_resp)
                return {"status": "error", "message": "No second dialog with '继续发表'"}

            page.locator('.weui-desktop-dialog__wrp .weui-desktop-dialog button:has-text("继续发表")').first.click(timeout=10000)
            logger.info("Clicked '继续发表'! Publishing...")
            page.wait_for_timeout(15000)
            page.remove_listener("response", on_resp)

            success = any("masssend" in md.get("url", "") and '"ret":0' in md.get("body", "") for md in masssend_data)
            if success:
                logger.info("PUBLISH SUCCESS! '%s'", title)
                return {"status": "success", "title": title, "app_msg_id": app_msg_id}
            else:
                logger.warning("Publish result unclear. masssend_data: %s", json.dumps(masssend_data, ensure_ascii=False)[:500])
                return {"status": "unclear", "title": title, "app_msg_id": app_msg_id, "masssend_data": masssend_data}

        except Exception as exc:
            logger.error("Publish failed: %s", exc)
            return {"status": "error", "message": str(exc)}
        finally:
            browser.close()


def main():
    settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

    logger.info("=== Scheduled WeChat Publish Started ===")

    title = ""
    content = ""

    try:
        pipeline = ContentFactoryPipeline(settings, logger)
        result = pipeline.run()
        run_dir = result.get("run_dir", "")
        logger.info("Pipeline completed. Run dir: %s", run_dir)

        if run_dir:
            run_path = Path(run_dir)
            title_path = run_path / "review" / "title.txt"
            article_path = run_path / "article.html"

            if title_path.exists():
                title = title_path.read_text(encoding="utf-8", errors="replace").strip()

            if article_path.exists():
                content = article_path.read_text(encoding="utf-8", errors="replace").strip()

            if not content:
                article_md = run_path / "article.md"
                if article_md.exists():
                    md_text = article_md.read_text(encoding="utf-8", errors="replace").strip()
                    import markdown
                    content = markdown.markdown(md_text)

        if not title or not content:
            output_dir = settings.output_dir
            logger.info("run_dir empty or content missing, checking output dir: %s", output_dir)

            if not title:
                titles_file = output_dir / "titles.txt"
                if titles_file.exists():
                    lines = titles_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                    for line in lines:
                        stripped = line.strip()
                        if stripped and re.match(r'^\d+\.', stripped):
                            title = re.sub(r'^\d+\.\s*', '', stripped)
                            break
                    if not title and lines:
                        for line in lines:
                            stripped = line.strip()
                            if stripped and not stripped.startswith("全部") and not stripped.startswith("推荐"):
                                title = stripped
                                break
                    if title:
                        logger.info("Title from output/titles.txt: %s", title)

            if not content:
                article_md = output_dir / "article.md"
                if article_md.exists():
                    md_text = article_md.read_text(encoding="utf-8", errors="replace").strip()
                    if md_text:
                        import markdown
                        content = markdown.markdown(md_text)
                        logger.info("Content from output/article.md, len=%d", len(content))
    except Exception as exc:
        logger.warning("Pipeline failed: %s, using fallback content", exc)

    if not title:
        title = f"科技产业观察-{datetime.now().strftime('%m月%d日%H:%M')}"
        logger.info("Using fallback title: %s", title)

    if not content:
        now = datetime.now()
        content = (
            f'<p>发布时间：{now.strftime("%Y年%m月%d日 %H:%M")}。</p>'
            '<p>科技产业动态观察与分享。</p>'
            '<p>AI 技术持续推动产业变革，大模型应用落地加速。</p>'
            '<p>半导体产业链国产替代稳步推进。</p>'
            '<p>新能源与智能驾驶融合发展进入新阶段。</p>'
            '<p>欢迎关注后续更新。</p>'
        )
        logger.info("Using fallback content")

    logger.info("Publishing: %s", title)
    pub_result = publish_wechat_article(title, content, settings)
    logger.info("Publish result: %s", json.dumps(pub_result, ensure_ascii=False))

    result_path = settings.output_dir / f"wechat_publish_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    result_path.write_text(json.dumps(pub_result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
