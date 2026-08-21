"""从项目配置构造 OA Playwright 客户端。"""

from __future__ import annotations

from core.config.settings import Settings, get_settings
from core.oa.playwright_client import SeeyonPlaywrightClient


def build_oa_client(settings: Settings | None = None) -> SeeyonPlaywrightClient:
    settings = settings or get_settings()
    return SeeyonPlaywrightClient(
        settings.oa_base_url,
        settings.oa_username or "1号",
        settings.oa_password,
        timeout_ms=settings.oa_playwright_timeout_ms,
    )


def require_oa_login(page, client: SeeyonPlaywrightClient) -> None:
    result = client.login(page)
    if not result.success:
        raise RuntimeError(result.error_message or "OA 登录失败")
