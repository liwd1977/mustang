"""OA 网络连通性检测。"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import httpx

from core.config.settings import Settings, get_settings
from core.oa.playwright_env import resolve_oa_proxy


def _parse_host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def check_oa_reachable(settings: Settings | None = None, *, timeout: float = 15.0) -> tuple[bool, str]:
    """检测 OA 是否可达；若配置了代理则经代理探测（与浏览器行为一致）。"""
    settings = settings or get_settings()
    proxy = resolve_oa_proxy(settings)
    login_url = f"{settings.oa_base_url.rstrip('/')}/seeyon/main.do"

    if not proxy:
        host, port = _parse_host_port(settings.oa_base_url)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except OSError as exc:
            return False, (
                f"无法直连 OA 服务器 {host}:{port}（{exc}）。"
                "浏览器能访问时，请在 .env 配置 OA_PROXY（如 http://127.0.0.1:7897）。"
            )

    try:
        client_kwargs: dict = {"timeout": timeout, "follow_redirects": True}
        if proxy:
            client_kwargs["proxy"] = proxy
        resp = httpx.get(login_url, **client_kwargs)
        if resp.status_code >= 500:
            return False, f"OA 登录页返回 HTTP {resp.status_code}，服务可能异常。"
        via = f"经代理 {proxy}" if proxy else "直连"
        return True, f"OA 可达（{via}，HTTP {resp.status_code}）"
    except httpx.TimeoutException:
        hint = "请检查 OA_PROXY 是否与系统代理一致。" if proxy else "请配置 OA_PROXY 或接入内网/VPN。"
        return False, f"OA 登录页响应超时（>{timeout}s）。{hint}"
    except httpx.HTTPError as exc:
        return False, f"OA HTTP 探测失败：{exc}"
