"""Playwright 浏览器环境与 OA 代理配置。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from core.config.settings import Settings, get_settings

DEFAULT_VIEWPORT = {"width": 1600, "height": 1000}


def resolve_oa_proxy(settings: Settings | None = None) -> str:
    """解析 OA 访问代理（与浏览器系统代理对齐）。"""
    settings = settings or get_settings()
    if settings.oa_proxy.strip():
        return settings.oa_proxy.strip()
    _ensure = os.environ
    for name in ("OA_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = (_ensure.get(name) or "").strip()
        if value:
            return value
    return ""


def playwright_proxy_dict(settings: Settings | None = None) -> dict[str, str] | None:
    proxy_url = resolve_oa_proxy(settings)
    if not proxy_url:
        return None
    return {"server": proxy_url}


def check_playwright_chromium() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "未安装 playwright，请执行：pip install playwright"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True, ""
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            return False, (
                "Playwright 浏览器未安装。请在项目虚拟环境中执行：\n"
                "`venv\\Scripts\\python.exe -m playwright install chromium`"
            )
        return False, msg


@contextmanager
def oa_browser_session(
    settings: Settings | None = None,
    *,
    headless: bool = True,
) -> Iterator[tuple]:
    """启动带 OA 代理的 Playwright 会话，yield (playwright, browser, context, page)。"""
    from playwright.sync_api import sync_playwright

    settings = settings or get_settings()
    proxy = playwright_proxy_dict(settings)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_kwargs: dict = {"viewport": DEFAULT_VIEWPORT, "locale": "zh-CN"}
        if proxy:
            ctx_kwargs["proxy"] = proxy
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.set_default_timeout(settings.oa_playwright_timeout_ms)
        try:
            yield p, browser, context, page
        finally:
            context.close()
            browser.close()
