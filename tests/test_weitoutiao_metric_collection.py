"""微头条补采不得在导航到微头条列表页之后又跳回图文管理页。

生产证据（2026-10-21 定时运行 run 39525806166 的 app.log）：

    09:15:28 Clicked Toutiao content manage entry: text=作品管理      ← 图文采集
    09:15:28 Advanced to next page via Toutiao pagination control.    ← 翻页
    09:15:31 Advanced to next page ...
    09:15:36 Advanced to next page ...
    09:15:51 Clicked Toutiao content manage entry: text=作品管理      ← 微头条补采又点了一次
    09:15:57 Weitoutiao list page exposed 0 rows.                    ← 结果必然 0 行

`_collect_weitoutiao_rows()` 先 `goto(WEITOUTIAO_LIST_URL)`（这已经是微头条列表页），
却又调用 `_enter_content_manage(page)`。该函数会点击「作品管理」，找不到时还会
兜底 `goto(.../manage/content/all)` —— 两者都会把页面从微头条列表导航回图文页。
于是每次补采都在错误的页面上找微头条行，恒为 0 行；已发布的 15 条微头条里只有
1 条（碰巧出现在图文列表）被采到。

这些用例不连接真实浏览器：用一个记录调用的假 page 断言「导航到微头条页之后
不再发生任何跳转/点击」。
"""
from __future__ import annotations

from typing import Any

from toutiao_content_factory.services.performance_service import PerformanceService


class _FakeLocator:
    def __init__(self) -> None:
        self.clicks = 0

    def count(self) -> int:
        return 1

    def is_visible(self, timeout: int | None = None) -> bool:
        return True

    def click(self, timeout: int | None = None) -> None:
        self.clicks += 1


class _FakePage:
    """Records navigation and clicks so the test can prove no detour happened."""

    def __init__(self, rows_per_frame: list[dict[str, str]] | None = None) -> None:
        self.gotos: list[str] = []
        self.clicks: list[str] = []
        self._rows = rows_per_frame or []
        self._url = "about:blank"
        self.main_frame = self
        self.frames: list[Any] = []

    # ---- navigation ----
    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.gotos.append(url)
        self._url = url

    def wait_for_timeout(self, ms: int) -> None:
        return None

    # ---- interaction used by _enter_content_manage ----
    def locator(self, selector: str) -> _FakeLocator:
        locator = _FakeLocator()
        original_click = locator.click

        def click(timeout: int | None = None) -> None:
            self.clicks.append(selector)
            original_click(timeout)

        locator.click = click  # type: ignore[method-assign]
        return locator

    # ---- extraction surface used by _extract_metric_rows_from_frames ----
    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        return None

    def query_selector_all(self, selector: str) -> list[Any]:
        return []

    def evaluate(self, script: str, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
        # 真实实现是在 frame 里 evaluate 一个 JS 函数返回行数组。
        return self._rows


def _service() -> PerformanceService:
    service = object.__new__(PerformanceService)
    service.logger = __import__("logging").getLogger("test_weitoutiao")
    service.MAX_PAGES = 5
    return service


def test_weitoutiao_collection_does_not_navigate_away_after_reaching_the_list() -> None:
    """导航到微头条列表后，不允许再点击「作品管理」或跳到 content/all。"""
    page = _FakePage()
    service = _service()

    service._collect_weitoutiao_rows(page=page, row_selectors=("[class*='article-card']",))

    assert page.gotos == [service.WEITOUTIAO_LIST_URL], (
        f"微头条补采只应导航一次到微头条列表页，实际: {page.gotos}"
    )
    assert page.clicks == [], (
        f"到达微头条列表页后不应再点击任何入口（会跳回图文页），实际点击: {page.clicks}"
    )


def test_weitoutiao_collection_returns_rows_when_the_page_has_them() -> None:
    page = _FakePage(rows_per_frame=[{"title": "微头条一", "views": "120"}])
    service = _service()

    rows = service._collect_weitoutiao_rows(page=page, row_selectors=("[class*='article-card']",))

    assert rows, "页面有行时必须返回行，而不是空列表"
    assert page.gotos == [service.WEITOUTIAO_LIST_URL]


def test_weitoutiao_collection_is_non_fatal_on_navigation_error() -> None:
    class _BoomPage(_FakePage):
        def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
            raise RuntimeError("navigation blew up")

    service = _service()
    assert service._collect_weitoutiao_rows(page=_BoomPage(), row_selectors=("x",)) == []
