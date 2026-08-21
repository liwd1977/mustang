"""致远 OA Playwright 浏览器自动化客户端。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.oa.navigation import safe_goto
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


@dataclass
class OALoginResult:
    success: bool
    final_url: str = ""
    error_message: str = ""


@dataclass
class OATemplateResult:
    success: bool
    final_url: str = ""
    title: str = ""
    has_login_page: bool = False
    has_form_content: bool = False
    form_input_count: int = 0
    iframe_count: int = 0
    snippet: str = ""
    error_message: str = ""
    screenshot_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _is_login_page(page: Page) -> bool:
    try:
        return page.locator("#login_username").count() > 0
    except Exception:
        return False


def _collect_page_signals(page: Page) -> dict[str, Any]:
    """采集页面特征，用于判断是否为协同表单页。"""
    return page.evaluate(
        """() => {
            const text = document.body ? document.body.innerText : '';
            const inputs = document.querySelectorAll('input, textarea, select');
            const iframes = document.querySelectorAll('iframe');
            const lower = text.toLowerCase();
            return {
                title: document.title || '',
                inputCount: inputs.length,
                iframeCount: iframes.length,
                hasCollaboration: lower.includes('协同') || location.href.includes('collaboration'),
                hasFormKeyword: lower.includes('表单') || lower.includes('模板'),
                hasNewColl: location.href.includes('newColl'),
                snippet: text.replace(/\\s+/g, ' ').slice(0, 500),
            };
        }"""
    )


class SeeyonPlaywrightClient:
    """使用 Playwright 登录致远 OA 并访问页面。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        headless: bool = True,
        timeout_ms: int = 60_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.login_url = f"{self.base_url}/seeyon/main.do"
        self.username = username
        self.password = password
        self.headless = headless
        self.timeout_ms = timeout_ms

    def login(self, page: Page) -> OALoginResult:
        if not self.username or not self.password:
            return OALoginResult(
                success=False,
                error_message="未配置 OA 账号密码，请在 .env 中设置 OA_USERNAME / OA_PASSWORD",
            )

        try:
            safe_goto(page, self.login_url, timeout=self.timeout_ms)
        except PlaywrightTimeoutError:
            return OALoginResult(
                success=False,
                final_url=page.url,
                error_message=(
                    f"连接 OA 超时（{self.timeout_ms // 1000}s）：{self.login_url}。"
                    "请确认已接入内网/VPN，OA 服务可访问。"
                ),
            )

        try:
            page.wait_for_selector("#login_username", timeout=self.timeout_ms)
        except PlaywrightTimeoutError:
            return OALoginResult(
                success=False,
                final_url=page.url,
                error_message="OA 页面已打开，但未找到登录框，可能地址或页面结构有变",
            )

        page.fill("#login_username", self.username)
        page.fill("#login_password1", self.password)
        page.click("#login_button")

        # 登录成功后会离开登录页，进入 main 门户
        try:
            page.wait_for_function(
                "() => !document.querySelector('#login_username')",
                timeout=self.timeout_ms,
            )
        except PlaywrightTimeoutError:
            if _is_login_page(page):
                return OALoginResult(
                    success=False,
                    final_url=page.url,
                    error_message="仍在登录页，可能账号或密码错误",
                )
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 30_000))
        except PlaywrightTimeoutError:
            pass

        if _is_login_page(page):
            return OALoginResult(
                success=False,
                final_url=page.url,
                error_message="登录后仍停留在登录页",
            )

        return OALoginResult(success=True, final_url=page.url)

    def fetch_template(
        self,
        page: Page,
        template_url: str,
        *,
        screenshot_path: str | None = None,
    ) -> OATemplateResult:
        safe_goto(page, template_url, timeout=self.timeout_ms)
        page.wait_for_timeout(3000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 45_000))
        except PlaywrightTimeoutError:
            pass

        if screenshot_path:
            page.screenshot(path=screenshot_path, full_page=True)

        signals = _collect_page_signals(page)
        has_login = _is_login_page(page)
        has_form = (
            signals["inputCount"] > 3
            or signals["iframeCount"] > 0
            or (signals["hasNewColl"] and not has_login)
        )
        success = not has_login and has_form

        error_message = ""
        if has_login:
            error_message = "访问模板时跳回登录页，会话可能失效"
        elif not has_form:
            error_message = "未检测到足够的表单元素或 iframe"

        return OATemplateResult(
            success=success,
            final_url=page.url,
            title=signals.get("title", ""),
            has_login_page=has_login,
            has_form_content=has_form,
            form_input_count=int(signals.get("inputCount", 0)),
            iframe_count=int(signals.get("iframeCount", 0)),
            snippet=str(signals.get("snippet", "")),
            error_message=error_message,
            screenshot_path=screenshot_path or "",
            extra=signals,
        )

    def probe_template(self, template_url: str, *, screenshot_path: str | None = None) -> tuple[OALoginResult, OATemplateResult | None]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                login_result = self.login(page)
                if not login_result.success:
                    return login_result, None
                template_result = self.fetch_template(page, template_url, screenshot_path=screenshot_path)
                return login_result, template_result
            finally:
                context.close()
                browser.close()
