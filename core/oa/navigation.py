"""Playwright 页面导航容错（Seeyon OA 常见 ERR_ABORTED）。"""

from __future__ import annotations

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

COLLAB_READY_SELECTORS = (
    "#refresh2_a",
    "#refresh2",
    "[title='调用模板']",
    "text=调用模板",
)


def safe_goto(page: Page, url: str, *, timeout: int = 90_000) -> None:
    """打开 URL；ERR_ABORTED 时不立即失败，改为等待页面就绪元素。"""
    last_error: Exception | None = None
    keyword = _url_keyword(url)
    ready_selectors = COLLAB_READY_SELECTORS if keyword == "collaboration" else None

    for attempt in range(3):
        for navigate in (_goto_dom, _goto_js):
            try:
                navigate(page, url, timeout=timeout)
            except PlaywrightError as exc:
                last_error = exc
                if not _is_aborted_navigation(str(exc)):
                    last_error = exc
            except Exception as exc:
                last_error = exc

            page.wait_for_timeout(2000)
            if ready_selectors and _wait_selectors(page, ready_selectors, timeout_ms=12_000):
                return
            if _landed_on_target(page, keyword):
                return

        page.wait_for_timeout(1500)

    if ready_selectors and _wait_selectors(page, ready_selectors, timeout_ms=30_000):
        return
    if _landed_on_target(page, keyword):
        return
    raise RuntimeError(f"无法打开页面 {url}：{last_error}")


def open_collaboration_page(page: Page, template_url: str, *, timeout: int = 90_000) -> None:
    """打开协同新建页，以「调用模板」按钮出现为成功标志。"""
    safe_goto(page, template_url, timeout=timeout)
    if not _wait_selectors(page, COLLAB_READY_SELECTORS, timeout_ms=20_000):
        raise RuntimeError("协同新建页未加载完成，未找到「调用模板」按钮")


def _goto_dom(page: Page, url: str, *, timeout: int) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)


def _goto_js(page: Page, url: str, *, timeout: int) -> None:
    page.evaluate("u => window.location.assign(u)", url)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 45_000))
    except PlaywrightError:
        pass


def _wait_selectors(page: Page, selectors: tuple[str, ...], *, timeout_ms: int) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return True
            except Exception:
                continue
        page.wait_for_timeout(400)
    return False


def _url_keyword(url: str) -> str:
    if "collaboration" in url:
        return "collaboration"
    if "main.do" in url:
        return "main.do"
    return "seeyon"


def _is_aborted_navigation(message: str) -> bool:
    tokens = ("ERR_ABORTED", "NS_BINDING_ABORTED", "Navigation failed", "Page.goto")
    return any(t in message for t in tokens)


def _landed_on_target(page: Page, keyword: str) -> bool:
    current = page.url or ""
    return "seeyon" in current and (keyword in current or keyword == "seeyon")
