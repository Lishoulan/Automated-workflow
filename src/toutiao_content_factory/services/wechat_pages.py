"""WeChat Page Object models — backward-compatible facade.

All selector definitions have been migrated to
``toutiao_content_factory.constants.selectors``.  This module re-exports
them so that existing imports continue to work without changes.
"""
from __future__ import annotations

# Re-export PageSelectors and all platform page objects from the
# centralized selector registry so existing code keeps working.
from toutiao_content_factory.constants.selectors import (  # noqa: F401
    PageSelectors,
    WECHAT_LOGIN_PAGE as LOGIN_PAGE,
    WECHAT_HOME_PAGE as HOME_PAGE,
    WECHAT_DRAFT_LIST_PAGE as DRAFT_LIST_PAGE,
    WECHAT_EDITOR_PAGE as EDITOR_PAGE,
    WECHAT_COVER_DIALOG as COVER_DIALOG,
    WECHAT_PUBLISH_DIALOG as PUBLISH_DIALOG,
    TOUTIAO_HOME_PAGE,
    TOUTIAO_EDITOR_PAGE,
)


class WechatPages:
    """Centralized WeChat MP page selectors (facade)."""

    LOGIN_PAGE = LOGIN_PAGE
    HOME_PAGE = HOME_PAGE
    DRAFT_LIST_PAGE = DRAFT_LIST_PAGE
    EDITOR_PAGE = EDITOR_PAGE
    COVER_DIALOG = COVER_DIALOG
    PUBLISH_DIALOG = PUBLISH_DIALOG

    @classmethod
    def all_pages(cls) -> dict[str, PageSelectors]:
        """Get all page selectors as a dict."""
        return {
            "login": cls.LOGIN_PAGE,
            "home": cls.HOME_PAGE,
            "draft_list": cls.DRAFT_LIST_PAGE,
            "editor": cls.EDITOR_PAGE,
            "cover_dialog": cls.COVER_DIALOG,
            "publish_dialog": cls.PUBLISH_DIALOG,
        }

    @classmethod
    def get_selector(cls, page_name: str, element_name: str) -> list[str]:
        """Convenience method to get selectors for a page element."""
        pages = cls.all_pages()
        page = pages.get(page_name)
        if page is None:
            return []
        return page.get(element_name)


class ToutiaoPages:
    """Centralized Toutiao MP page selectors (facade)."""

    HOME_PAGE = TOUTIAO_HOME_PAGE
    EDITOR_PAGE = TOUTIAO_EDITOR_PAGE

    @classmethod
    def all_pages(cls) -> dict[str, PageSelectors]:
        return {
            "home": cls.HOME_PAGE,
            "editor": cls.EDITOR_PAGE,
        }
