from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


class FirstReleaseAdapter:
    FIRST_RELEASE_CHECKBOX_SELECTORS = [
        'text=声明首发',
        'text=头条首发',
        'text=单篇勾选可享首发激励',
        '[data-testid="first-release-checkbox"]',
        'input[type="checkbox"][name*="first"]',
        'input[type="checkbox"][name*="exclusive"]',
    ]

    AIGC_CHECKBOX_SELECTORS = [
        'text=内容由AI生成',
        'text=AI生成内容',
        'text=AIGC',
        '[data-testid="aigc-checkbox"]',
        'input[type="checkbox"][name*="aigc"]',
        'input[type="checkbox"][name*="ai_generated"]',
    ]

    def __init__(self, enable_first_release: bool = True, enable_aigc_declaration: bool = True) -> None:
        self.enable_first_release = enable_first_release
        self.enable_aigc_declaration = enable_aigc_declaration

    async def fill_publish_options(self, page: Any) -> dict[str, Any]:
        result = {
            "first_release_checked": False,
            "aigc_checked": False,
            "errors": [],
        }

        if self.enable_first_release:
            checked = await self._check_first_release(page)
            result["first_release_checked"] = checked
            if not checked:
                result["errors"].append("Failed to check first-release checkbox")

        if self.enable_aigc_declaration:
            checked = await self._check_aigc_declaration(page)
            result["aigc_checked"] = checked
            if not checked:
                result["errors"].append("Failed to check AIGC declaration checkbox")

        return result

    async def _check_first_release(self, page: Any) -> bool:
        for selector in self.FIRST_RELEASE_CHECKBOX_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.is_visible():
                    if not await locator.is_checked():
                        await locator.check()
                    logger.info("First-release checkbox checked with selector: %s", selector)
                    return True
            except Exception:
                continue
        logger.warning("Could not find first-release checkbox on page")
        return False

    async def _check_aigc_declaration(self, page: Any) -> bool:
        for selector in self.AIGC_CHECKBOX_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.is_visible():
                    if not await locator.is_checked():
                        await locator.check()
                    logger.info("AIGC declaration checkbox checked with selector: %s", selector)
                    return True
            except Exception:
                continue
        logger.warning("Could not find AIGC declaration checkbox on page")
        return False

    @staticmethod
    def get_publish_script() -> str:
        return """
        // Auto-check first release and AIGC checkboxes
        (function() {
            const firstReleaseLabels = ['声明首发', '头条首发', '单篇勾选可享首发激励'];
            const aigcLabels = ['内容由AI生成', 'AI生成内容', 'AIGC'];

            function checkCheckboxByLabel(labels) {
                const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                for (const cb of checkboxes) {
                    const parent = cb.closest('label') || cb.parentElement;
                    if (parent) {
                        const text = parent.textContent || '';
                        for (const label of labels) {
                            if (text.includes(label)) {
                                if (!cb.checked) cb.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }

            checkCheckboxByLabel(firstReleaseLabels);
            checkCheckboxByLabel(aigcLabels);
        })();
        """
