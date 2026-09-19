"""封面校验判定的回归测试。

2026-09-17：云端发布连续 3 次卡在 `Cover verification failed before publish.`。
取回编辑器截图证实封面区还是空的 `+`、右侧写着「已上传 0 张图片」，
但日志里**没有** "All cover upload strategies failed" —— 说明上传策略被判成了成功。

根因：`_is_cover_applied()` 还用了三条**间接证据**（`+` 不在 / 预览按钮在 / loading
动画在）来推断封面已应用，与发布校验用的「`.article-cover img` 可见」不是同一标准。
这些测试把「只认可见图片」这条标准钉住。
"""

from __future__ import annotations

import logging
from pathlib import Path

from toutiao_content_factory.services.publish_service import PublishService


class _FakeLocator:
    def __init__(self, *, visible: bool = False, count: int = 1) -> None:
        self._visible = visible
        self._count = count

    @property
    def first(self) -> "_FakeLocator":
        # `_is_cover_applied` 走的是 `page.locator(sel).first.is_visible(...)`。
        # 少了这个属性会抛 AttributeError，被那里的 `except Exception: continue` 吞掉，
        # 于是判定**恒为 False** —— 测试会给出假阴性。
        return self

    def is_visible(self, timeout: int | None = None) -> bool:
        return self._visible

    def count(self) -> int:
        return self._count


class _FakePage:
    """只回答 `_is_cover_applied` 会问的那几个选择器。"""

    def __init__(self, visible_selectors=(), *, add_visible=True, preview_visible=True) -> None:
        self.visible_selectors = set(visible_selectors)
        self.add_visible = add_visible
        self.preview_visible = preview_visible

    def locator(self, selector: str) -> _FakeLocator:
        if selector == ".article-cover-add":
            return _FakeLocator(visible=self.add_visible)
        if selector == ".article-cover-preview .show-dialog-btn":
            return _FakeLocator(visible=self.preview_visible)
        return _FakeLocator(visible=selector in self.visible_selectors)


def _service(app_settings) -> PublishService:
    return PublishService(app_settings, logging.getLogger("cover-verification-test"))


def test_cover_counts_as_applied_only_when_a_visible_image_exists(app_settings) -> None:
    service = _service(app_settings)

    assert service._is_cover_applied(_FakePage({".article-cover img"})) is True
    assert service._is_cover_applied(_FakePage()) is False


def test_missing_add_button_alone_is_not_evidence_of_a_cover(app_settings) -> None:
    """`+` 不在（页面没渲染完、被遮挡）不能当作「封面已应用」。"""

    service = _service(app_settings)
    page = _FakePage(add_visible=False, preview_visible=False)

    assert service._is_cover_applied(page) is False


def test_preview_button_visibility_alone_is_not_evidence_of_a_cover(app_settings) -> None:
    """预览按钮在没有封面时也存在（只是 disabled），不能当作证据。"""

    service = _service(app_settings)
    page = _FakePage(add_visible=True, preview_visible=True)

    assert service._is_cover_applied(page) is False


def test_loading_spinner_is_not_evidence_of_a_cover(app_settings) -> None:
    """`.byte-spin` 是加载动画，转圈不等于封面已应用。"""

    service = _service(app_settings)
    page = _FakePage({".article-cover .byte-spin img"})

    assert service._is_cover_applied(page) is False


def test_cover_upload_trusts_the_page_not_the_strategy(app_settings, monkeypatch) -> None:
    """策略自称成功、页面上却没有封面时，必须判失败并转无封面模式。"""

    service = _service(app_settings)
    notes: list[str] = []
    page = _FakePage()

    monkeypatch.setattr(service, "_dismiss_ai_assistant_drawer", lambda *a, **k: None)
    monkeypatch.setattr(service, "_hide_ai_drawer_mask_css", lambda *a, **k: None)
    monkeypatch.setattr(service, "_ensure_single_cover_mode", lambda *a, **k: None)
    monkeypatch.setattr(service, "_clear_existing_cover", lambda *a, **k: None)

    called: list[str] = []
    monkeypatch.setattr(
        service,
        "_upload_cover_via_file_chooser",
        lambda *a, **k: (called.append("file_chooser"), True)[1],
    )
    monkeypatch.setattr(
        service,
        "_upload_cover_via_direct_input",
        lambda *a, **k: (called.append("direct_input"), True)[1],
    )
    fallback: list[bool] = []
    monkeypatch.setattr(service, "_ensure_no_cover_mode", lambda *a, **k: fallback.append(True))

    applied = service._apply_cover_image(page, Path("cover.png"), notes)

    assert applied is False
    assert called == ["file_chooser", "direct_input"]
    assert fallback == [True]
    assert any("page_has_cover=False" in item for item in notes)
    assert any("All cover upload strategies failed" in item for item in notes)


def test_cover_upload_returns_true_when_the_page_really_has_a_cover(
    app_settings, monkeypatch
) -> None:
    service = _service(app_settings)
    notes: list[str] = []
    page = _FakePage({".article-cover img"})

    monkeypatch.setattr(service, "_dismiss_ai_assistant_drawer", lambda *a, **k: None)
    monkeypatch.setattr(service, "_hide_ai_drawer_mask_css", lambda *a, **k: None)
    monkeypatch.setattr(service, "_ensure_single_cover_mode", lambda *a, **k: None)
    monkeypatch.setattr(service, "_clear_existing_cover", lambda *a, **k: None)
    monkeypatch.setattr(service, "_upload_cover_via_file_chooser", lambda *a, **k: True)
    monkeypatch.setattr(service, "_upload_cover_via_direct_input", lambda *a, **k: True)
    monkeypatch.setattr(service, "_ensure_no_cover_mode", lambda *a, **k: None)

    applied = service._apply_cover_image(page, Path("cover.png"), notes)

    assert applied is True
    assert any("Applied cover image" in item for item in notes)


def test_cover_js_fallback_is_scoped_inside_the_cover_area(app_settings) -> None:
    """JS 兜底必须在 `.article-cover` 区域内找图，不能在全文档找。

    旧写法 `document.querySelector('.article-cover-img-wrap img')` 是**全文档**查询，
    封面区之外只要有同名元素就会被判成「封面已应用」—— 和上面三条间接证据是同一类错误。
    """

    service = _service(app_settings)
    captured: list[str] = []

    class _Page:
        def locator(self, selector: str) -> _FakeLocator:
            return _FakeLocator(visible=False)

        def evaluate(self, script: str):
            captured.append(script)
            return False

    assert service._verify_cover_applied(_Page()) is False
    assert captured, "expected the JS fallback to be attempted"
    script = captured[0]
    assert "document.querySelector('.article-cover')" in script
    assert "document.querySelector('.article-cover-img-wrap img')" not in script
