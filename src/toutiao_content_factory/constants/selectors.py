"""Centralized selector registry for all platform RPA automation.

All CSS selectors, XPath expressions, and text-based locators used by
Playwright browser automation are defined here, organized by platform
and page/dialog.  When a platform updates its UI, only this file needs
to change — the calling service code stays the same.

Migration note:
  - WeChat selectors previously lived in wechat_pages.py and inline in
    wechat_service.py JS evaluate blocks.
  - Toutiao selectors previously lived as class constants in
    publish_service.py.
  - Those inline usages are now replaced with references to this module.

Data loading:
  - If config/selectors.yaml exists, selectors are loaded from it.
  - Otherwise, hardcoded defaults are used (backward compatibility).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/toutiao_content_factory/constants -> project root
_SELECTORS_YAML_PATH = _PROJECT_ROOT / "config" / "selectors.yaml"


def _load_yaml_data() -> dict[str, Any] | None:
    """Load selector data from YAML file if it exists."""
    if not _SELECTORS_YAML_PATH.exists():
        logger.debug("selectors.yaml not found at %s; using hardcoded defaults.", _SELECTORS_YAML_PATH)
        return None
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; cannot load selectors.yaml, using hardcoded defaults.")
        return None
    try:
        with open(_SELECTORS_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        logger.info("Loaded selectors from %s", _SELECTORS_YAML_PATH)
        return data
    except Exception as exc:
        logger.warning("Failed to load selectors.yaml: %s; using hardcoded defaults.", exc)
        return None


def _build_page_selectors_from_yaml(
    yaml_data: dict, platform: str, page_name: str, fallback: PageSelectors | None = None
) -> PageSelectors | None:
    """Build a PageSelectors instance from YAML data for a given platform and page."""
    platform_data = yaml_data.get(platform)
    if not platform_data:
        return fallback
    page_data = platform_data.get(page_name)
    if not page_data:
        return fallback
    # Convert YAML lists to dict[str, list[str]]
    selectors: dict[str, list[str]] = {}
    for element_name, selector_list in page_data.items():
        if isinstance(selector_list, list):
            selectors[element_name] = selector_list
        elif isinstance(selector_list, str):
            selectors[element_name] = [selector_list]
    return PageSelectors(name=f"{platform}_{page_name}", selectors=selectors)


# ---------------------------------------------------------------------------
# Generic container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageSelectors:
    """Selectors for a single page/dialog, with fallback chains."""

    name: str
    selectors: dict[str, list[str]]

    def get(self, element_name: str) -> list[str]:
        """Get selectors for an element, with fallback to empty list."""
        return self.selectors.get(element_name, [])

    def first(self, element_name: str) -> str:
        """Get the first selector for an element."""
        selectors = self.get(element_name)
        return selectors[0] if selectors else ""


# ========================================================================
# Hardcoded defaults (used when YAML is not available)
# ========================================================================

_DEFAULTS: dict[str, Any] = {
    "wechat": {
        "login_page": PageSelectors(
            name="wechat_login",
            selectors={
                "username_input": [
                    "input[name='account']",
                    "input[type='text']",
                ],
                "password_input": [
                    "input[name='password']",
                    "input[type='password']",
                ],
                "login_button": [
                    "a.btn_login",
                    "button:has-text('登录')",
                ],
                "qrcode_container": [
                    ".qrcode",
                    "div.qrcode-img",
                ],
                "qrcode_image": [
                    'img[src*="/cgi-bin/verifyqrcode"]',
                ],
                "account_input": [
                    'input[name="account"]',
                ],
                "password_input_direct": [
                    'input[type="password"]',
                ],
                "login_page_text": [
                    "text=微信公众平台",
                    "text=扫码登录",
                ],
            },
        ),
        "home_page": PageSelectors(
            name="wechat_home",
            selectors={
                "content_management_menu": [
                    "a:has-text('内容管理')",
                    "a[href*='appmsg']",
                    "text=内容管理",
                ],
                "draft_menu": [
                    "a:has-text('草稿箱')",
                    "a[href*='appmsgt']",
                    "text=草稿箱",
                ],
            },
        ),
        "draft_list_page": PageSelectors(
            name="wechat_draft_list",
            selectors={
                "new_article_button": [
                    "a.weui-desktop-btn__content",
                    "a:has-text('新的创作')",
                    "a:has-text('写文章')",
                    "button:has-text('新建')",
                ],
                "article_title_link": [
                    "a.article_title",
                    "td a",
                ],
                "draft_title_cover": [
                    ".weui-desktop-publish__cover__title",
                ],
                "draft_title_link": [
                    "a.weui-desktop-publish__cover__title",
                ],
                "search_input": [
                    "input[placeholder*='搜索']",
                    "input[placeholder*='标题']",
                    "input[type='search']",
                ],
            },
        ),
        "editor_page": PageSelectors(
            name="wechat_editor",
            selectors={
                "title_input": [
                    "#title",
                    "input#title",
                    "input[placeholder*='标题']",
                ],
                "editor_area": [
                    ".ProseMirror",
                    "#edui1_body",
                    "div.edui-body-container",
                    "div[contenteditable='true']",
                ],
                "cover_button": [
                    ".js_cover",
                    "a:has-text('封面')",
                    "span:has-text('选择封面')",
                ],
                "cover_btn_area": [
                    ".js_cover_btn_area",
                ],
                "cover_file_input": [
                    '.js_cover_btn_area input[type="file"]',
                    '#js_cover_area input[type="file"]',
                    '.select-cover__btn input[type="file"]',
                    'input[type="file"][accept*="image"]',
                ],
                "digest_input": [
                    "#digest",
                    "textarea#digest",
                    "textarea[placeholder*='摘要']",
                ],
                "author_input": [
                    "#author",
                    "input#author",
                ],
                "save_button": [
                    "#js_submit",
                    "button:has-text('保存')",
                    "a:has-text('保存')",
                ],
                "publish_button": [
                    "#js_send",
                    "#js_submit button",
                    "button.mass_send",
                    ".tool_area_wrp .btn_default",
                    '.tool_area_wrp button[type="button"]',
                ],
                "image_library_link": [
                    ".js_imagedialog",
                ],
            },
        ),
        "cover_dialog": PageSelectors(
            name="wechat_cover_dialog",
            selectors={
                "upload_tab": [
                    "a:has-text('上传')",
                    "li:has-text('上传')",
                ],
                "file_input": [
                    "input[type='file'][accept*='image']",
                    "input[type='file']",
                ],
                "select_existing_tab": [
                    "a:has-text('从正文选择')",
                    "li:has-text('从正文选择')",
                ],
                "crop_confirm_button": [
                    ".js_crop_confirm",
                    "button:has-text('完成裁剪')",
                    "button:has-text('确定')",
                    "a:has-text('完成')",
                ],
                "cover_confirm_button": [
                    ".js_cover_confirm",
                    "button:has-text('确定')",
                    "a:has-text('确定')",
                ],
                "upload_file_text_button": [
                    "text=上传文件",
                    "text=上传图片",
                    "text=本地上传",
                ],
                "image_item": [
                    ".img_item",
                    ".image_item",
                    ".pic_item",
                    "[class*='img_item']",
                ],
                "confirm_button": [
                    "button:has-text('确定')",
                ],
                "cover_area_has_image": [
                    ".js_cover_btn_area",
                ],
                "choose_cover_text": [
                    "text=选择封面",
                    "text=拖拽或选择封面",
                ],
            },
        ),
        "publish_dialog": PageSelectors(
            name="wechat_publish_dialog",
            selectors={
                "dialog_wrapper": [
                    ".weui-desktop-dialog__wrp",
                    "div[role='dialog']",
                    ".weui-dialog",
                ],
                "dialog_inner": [
                    ".weui-desktop-dialog",
                ],
                "confirm_publish_button": [
                    ".mass_send_dialog .btn_primary",
                    ".mass_send_dialog .weui-desktop-btn_primary",
                    ".dialog_op .btn_primary",
                    ".popover .btn_primary.jsPopoverBt",
                    "button:has-text('群发')",
                    "button:has-text('确认')",
                    "a:has-text('确认')",
                ],
                "continue_publish_button": [
                    'button:has-text("继续发表")',
                ],
                "cancel_button": [
                    "button:has-text('取消')",
                    "a:has-text('取消')",
                ],
                "success_indicator": [
                    "text=发布成功",
                    "text=已发送",
                    ".weui-desktop-toast__content",
                ],
                "success_toast": [
                    ".weui-desktop-toast",
                    "[class*='toast']",
                    "[class*='success']",
                ],
                "page_tips": [
                    ".page_tips",
                    ".global_error",
                    ".toast",
                ],
            },
        ),
        "dismiss_dialog": PageSelectors(
            name="wechat_dismiss_dialog",
            selectors={
                "dialog_close_button": [
                    ".weui-desktop-dialog__close-btn",
                ],
                "img_picker_close": [
                    ".weui-desktop-dialog_img-picker .weui-desktop-dialog__close-btn",
                ],
                "img_picker": [
                    ".weui-desktop-dialog_img-picker",
                ],
            },
        ),
        "original_declaration": PageSelectors(
            name="wechat_original_declaration",
            selectors={
                "original_checkbox": [
                    "label:has-text('原创')",
                    "label:has-text('声明原创')",
                    "label:has-text('原创声明')",
                    ".weui-desktop-form__check-label:has-text('原创')",
                    "[role='checkbox']:has-text('原创')",
                    "text=原创",
                ],
            },
        ),
    },
    "toutiao": {
        "home_page": PageSelectors(
            name="toutiao_home",
            selectors={
                "new_post_button": [
                    "a:has-text('发文')",
                    "button:has-text('发文')",
                    "a:has-text('写文章')",
                    "button:has-text('写文章')",
                    "text=发布文章",
                ],
            },
        ),
        "editor_page": PageSelectors(
            name="toutiao_editor",
            selectors={
                "title_input": [
                    ".left-column .publish-editor-title-wrapper textarea",
                    ".publish-editor-title-wrapper textarea",
                    ".assistant-title .title-wrapper textarea",
                    ".publish-editor-title textarea",
                    ".editor-title textarea",
                    "textarea[autocomplete='off'][placeholder]",
                    "textarea[placeholder*='标题']",
                    "textarea",
                    "input[placeholder*='标题']",
                    "input[type='text']",
                ],
                "article_editor": [
                    ".left-column div.ProseMirror",
                    ".syl-editor-wrap div.ProseMirror",
                    ".syl-editor .ProseMirror",
                    "div.ProseMirror",
                    "div[contenteditable='true'][role='textbox']",
                    "div[contenteditable='true'][data-placeholder*='正文']",
                    "div[contenteditable='true']",
                ],
                "draft_status": [
                    ".publish-footer .footer-tip-save",
                    ".footer-draft-save .footer-tip-save",
                    ".footer-tip-save",
                ],
                "cover_upload": [
                    "input[type='file'][accept*='image']",
                    "input[type='file']",
                ],
                "image_insert_button": [
                    ".publish-editor .syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button.syl-toolbar-button",
                    ".publish-editor .syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button",
                    ".publish-editor .syl-editor-toolbar.visible.inline .syl-toolbar-tool.image",
                    ".syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button.syl-toolbar-button",
                    ".syl-editor-toolbar.visible.inline .syl-toolbar-tool.image button",
                    ".syl-editor-toolbar.visible.inline .syl-toolbar-tool.image",
                    ".syl-editor-toolbar.visible .syl-toolbar-tool.image button.syl-toolbar-button",
                    ".syl-editor-toolbar.visible .syl-toolbar-tool.image button",
                    ".syl-toolbar-tool.image .syl-toolbar-button",
                    ".syl-toolbar-tool.image button",
                    ".syl-toolbar-tool.image",
                    "[data-e2e='insertImage-btn']",
                    "[data-e2e='editorInsertImage-btn']",
                    ".syl-toolbar button[aria-label*='Image']",
                    ".syl-toolbar button[aria-label*='image']",
                    ".syl-toolbar button[aria-label*='图片']",
                    ".editor-toolbar button[aria-label*='Image']",
                    ".editor-toolbar button[aria-label*='image']",
                    ".editor-toolbar button[aria-label*='图片']",
                    "button[aria-label*='Image']",
                    "button[aria-label*='image']",
                    "button[aria-label*='图片']",
                    "button:has-text('图片')",
                ],
                "publish_button": [
                    "button:has-text('发布')",
                    "button:has-text('确认发布')",
                    "button:has-text('发表')",
                    "text=发布",
                ],
                "confirm_publish_button": [
                    "button:has-text('确认')",
                    "button:has-text('继续发布')",
                    "button:has-text('我知道了')",
                ],
                "success_indicator": [
                    "text=发布成功",
                    "text=提交成功",
                    "text=审核中",
                    "text=已提交",
                ],
                "ai_drawer_mask": [
                    "div.byte-drawer-mask",
                    "div.byte-drawer-wrapper.ai-assistant-drawer div.byte-drawer-mask",
                ],
                "ai_drawer_close": [
                    ".ai-assistant-drawer button[aria-label='关闭']",
                    ".ai-assistant-drawer .close-btn",
                    ".ai-assistant-drawer .byte-drawer-close",
                    ".ai-assistant-drawer button:has-text('关闭')",
                    ".ai-assistant-drawer [class*='close']",
                ],
            },
        ),
        "weitoutiao_page": PageSelectors(
            name="toutiao_weitoutiao",
            selectors={
                "editor": [
                    "div.ProseMirror",
                    "div[contenteditable='true'][data-placeholder*='微头条']",
                    "div[contenteditable='true'][data-placeholder*='正文']",
                    "div[contenteditable='true'][role='textbox']",
                    "div[contenteditable='true']",
                ],
                "publish_button": [
                    "button:has-text('发布')",
                    "button:has-text('发表')",
                    "button:has-text('发布微头条')",
                ],
                "success_indicator": [
                    "text=发布成功",
                    "text=提交成功",
                    "text=审核中",
                    "text=已发布",
                ],
            },
        ),
    },
    "editor_135": {
        "login_page": PageSelectors(
            name="editor_135_login",
            selectors={
                "login_button": [
                    "a:has-text('登录')",
                    "button:has-text('登录')",
                    ".login-btn",
                ],
                "wechat_login_button": [
                    "a:has-text('微信登录')",
                    "button:has-text('微信登录')",
                    ".wechat-login",
                ],
                "login_page_markers": [
                    "text=135编辑器",
                    "text=微信登录",
                ],
            },
        ),
        "editor_page": PageSelectors(
            name="editor_135_editor",
            selectors={
                "editor_area": [
                    "#ueditor_0",
                    ".edui-body-container",
                    "div[contenteditable='true']",
                ],
                "html_mode_button": [
                    "a:has-text('HTML')",
                    "button:has-text('HTML')",
                    ".edui-btn-html",
                    "a.edui-btn-html",
                ],
                "html_source_textarea": [
                    "textarea.edui-source-editor",
                    "textarea[class*='source']",
                    "textarea[class*='html']",
                ],
                "save_sync_button": [
                    "a:has-text('保存同步')",
                    "button:has-text('保存同步')",
                    ".save-sync-btn",
                    "#js_save_sync",
                ],
                "editor_content_area": [
                    "#ueditor_0",
                    ".edui-body-container",
                ],
            },
        ),
        "sync_dialog": PageSelectors(
            name="editor_135_sync_dialog",
            selectors={
                "title_input": [
                    "input[placeholder*='标题']",
                    "input[name='title']",
                    ".sync-title input",
                ],
                "cover_upload_button": [
                    "a:has-text('上传封面')",
                    "button:has-text('上传封面')",
                    ".cover-upload",
                    "input[type='file'][accept*='image']",
                ],
                "cover_file_input": [
                    "input[type='file'][accept*='image']",
                    "input[type='file']",
                ],
                "account_selector": [
                    "select[name='account']",
                    ".account-select",
                    "select",
                ],
                "save_button": [
                    "button:has-text('保存')",
                    "a:has-text('保存')",
                    ".btn-save",
                ],
                "sync_success_marker": [
                    "text=同步成功",
                    "text=保存成功",
                    "text=已同步",
                ],
                "dialog_wrapper": [
                    ".modal-dialog",
                    ".dialog-wrapper",
                    "div[role='dialog']",
                ],
            },
        ),
    },
}

# Default constant tuples
_DEFAULT_CONSTANTS: dict[str, dict[str, tuple[str, ...]]] = {
    "wechat": {
        "session_ready_markers": ("草稿箱", "新建图文", "素材管理", "发表记录", "首页"),
        "login_url_markers": ("/cgi-bin/loginpage", "/cgi-bin/home?t=home/login"),
        "dismiss_keywords": ("切换账号", "实名", "退出登录", "插件", "安全隐患"),
        "dismiss_actions": ("我知道了", "取消", "关闭"),
        "publish_confirm_texts": ("确定", "确认", "继续发布", "确认发布", "发表"),
        "publish_button_texts": ("发表", "发布", "群发", "提交", "确认发布"),
        "success_markers": ("发表成功", "发布成功", "群发成功", "已发送", "发布完成"),
    },
    "editor_135": {
        "session_ready_markers": ("135编辑器", "我的文章", "保存同步", "模板中心"),
        "login_url_markers": ("/login", "/user/login"),
        "sync_success_markers": ("同步成功", "保存成功", "已同步", "同步完成"),
    },
}


# ========================================================================
# Resolve selectors: YAML-first with hardcoded fallback
# ========================================================================

def _resolve_selectors() -> dict[str, Any]:
    """Resolve all page selectors, preferring YAML data when available."""
    yaml_data = _load_yaml_data()
    result: dict[str, Any] = {}

    for platform, pages in _DEFAULTS.items():
        result[platform] = {}
        for page_name, default_ps in pages.items():
            if yaml_data:
                resolved = _build_page_selectors_from_yaml(yaml_data, platform, page_name, default_ps)
            else:
                resolved = default_ps
            result[platform][page_name] = resolved

    return result


def _resolve_constants() -> dict[str, dict[str, tuple[str, ...]]]:
    """Resolve constant tuples, preferring YAML data when available."""
    yaml_data = _load_yaml_data()
    result: dict[str, dict[str, tuple[str, ...]]] = {}

    for platform, constants in _DEFAULT_CONSTANTS.items():
        result[platform] = {}
        for const_name, default_tuple in constants.items():
            if yaml_data:
                platform_data = yaml_data.get(platform, {})
                yaml_value = platform_data.get(const_name)
                if isinstance(yaml_value, list):
                    result[platform][const_name] = tuple(yaml_value)
                else:
                    result[platform][const_name] = default_tuple
            else:
                result[platform][const_name] = default_tuple

    return result


# Build resolved data at module load time
_resolved = _resolve_selectors()
_resolved_constants = _resolve_constants()

# ========================================================================
# WeChat (微信公众号) — module-level constants
# ========================================================================

WECHAT_LOGIN_PAGE = _resolved["wechat"]["login_page"]
WECHAT_HOME_PAGE = _resolved["wechat"]["home_page"]
WECHAT_DRAFT_LIST_PAGE = _resolved["wechat"]["draft_list_page"]
WECHAT_EDITOR_PAGE = _resolved["wechat"]["editor_page"]
WECHAT_COVER_DIALOG = _resolved["wechat"]["cover_dialog"]
WECHAT_PUBLISH_DIALOG = _resolved["wechat"]["publish_dialog"]
WECHAT_DISMISS_DIALOG = _resolved["wechat"]["dismiss_dialog"]
WECHAT_ORIGINAL_DECLARATION = _resolved["wechat"]["original_declaration"]

WECHAT_SESSION_READY_MARKERS = _resolved_constants["wechat"]["session_ready_markers"]
WECHAT_LOGIN_URL_MARKERS = _resolved_constants["wechat"]["login_url_markers"]
WECHAT_DISMISS_KEYWORDS = _resolved_constants["wechat"]["dismiss_keywords"]
WECHAT_DISMISS_ACTIONS = _resolved_constants["wechat"]["dismiss_actions"]
WECHAT_PUBLISH_CONFIRM_TEXTS = _resolved_constants["wechat"]["publish_confirm_texts"]
WECHAT_PUBLISH_BUTTON_TEXTS = _resolved_constants["wechat"]["publish_button_texts"]
WECHAT_SUCCESS_MARKERS = _resolved_constants["wechat"]["success_markers"]


# ========================================================================
# Toutiao (今日头条) — module-level constants
# ========================================================================

TOUTIAO_HOME_PAGE = _resolved["toutiao"]["home_page"]
TOUTIAO_EDITOR_PAGE = _resolved["toutiao"]["editor_page"]
TOUTIAO_WEITOUTIAO_PAGE = _resolved["toutiao"].get("weitoutiao_page", _DEFAULTS["toutiao"]["weitoutiao_page"])


# ========================================================================
# 135 Editor (135编辑器) — module-level constants
# ========================================================================

EDITOR_135_LOGIN_PAGE = _resolved.get("editor_135", {}).get("login_page", _DEFAULTS["editor_135"]["login_page"])
EDITOR_135_EDITOR_PAGE = _resolved.get("editor_135", {}).get("editor_page", _DEFAULTS["editor_135"]["editor_page"])
EDITOR_135_SYNC_DIALOG = _resolved.get("editor_135", {}).get("sync_dialog", _DEFAULTS["editor_135"]["sync_dialog"])

EDITOR_135_SESSION_READY_MARKERS = _resolved_constants.get("editor_135", {}).get("session_ready_markers", _DEFAULT_CONSTANTS["editor_135"]["session_ready_markers"])
EDITOR_135_LOGIN_URL_MARKERS = _resolved_constants.get("editor_135", {}).get("login_url_markers", _DEFAULT_CONSTANTS["editor_135"]["login_url_markers"])
EDITOR_135_SYNC_SUCCESS_MARKERS = _resolved_constants.get("editor_135", {}).get("sync_success_markers", _DEFAULT_CONSTANTS["editor_135"]["sync_success_markers"])


# ========================================================================
# Convenience accessors
# ========================================================================

def get_wechat_page(page_name: str) -> PageSelectors | None:
    """Look up a WeChat page selector set by name."""
    pages = {
        "login": WECHAT_LOGIN_PAGE,
        "home": WECHAT_HOME_PAGE,
        "draft_list": WECHAT_DRAFT_LIST_PAGE,
        "editor": WECHAT_EDITOR_PAGE,
        "cover_dialog": WECHAT_COVER_DIALOG,
        "publish_dialog": WECHAT_PUBLISH_DIALOG,
        "dismiss_dialog": WECHAT_DISMISS_DIALOG,
        "original_declaration": WECHAT_ORIGINAL_DECLARATION,
    }
    return pages.get(page_name)


def get_toutiao_page(page_name: str) -> PageSelectors | None:
    """Look up a Toutiao page selector set by name."""
    pages = {
        "home": TOUTIAO_HOME_PAGE,
        "editor": TOUTIAO_EDITOR_PAGE,
        "weitoutiao": TOUTIAO_WEITOUTIAO_PAGE,
    }
    return pages.get(page_name)


def get_editor_135_page(page_name: str) -> PageSelectors | None:
    """Look up a 135 Editor page selector set by name."""
    pages = {
        "login": EDITOR_135_LOGIN_PAGE,
        "editor": EDITOR_135_EDITOR_PAGE,
        "sync_dialog": EDITOR_135_SYNC_DIALOG,
    }
    return pages.get(page_name)
