"""致远 Seeyon OA 登录与表单模板访问客户端。"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class SeeyonLoginError(Exception):
    """登录失败。"""


@dataclass
class TemplateAccessResult:
    success: bool
    status_code: int
    final_url: str
    title: str = ""
    has_login_page: bool = False
    has_form_content: bool = False
    error_message: str = ""
    snippet: str = ""
    cookies: dict[str, str] = field(default_factory=dict)


def _extract_security_seed(html: str) -> str | None:
    match = re.search(r"var\s+_SecuritySeed\s*=\s*['\"]([^'\"]+)['\"]", html)
    return match.group(1) if match else None


def encrypt_login_password(password: str, security_seed: str) -> str:
    """
    模拟前端 CryptoJS.DES.encrypt(password, _SecuritySeed)。
    Seeyon V8 使用 DES-ECB + PKCS7，密钥为 SecuritySeed UTF-8 前 8 字节。
    """
    key = security_seed.encode("utf-8")[:8]
    if len(key) < 8:
        key = key.ljust(8, b"\0")
    cipher = DES.new(key, DES.MODE_ECB)
    encrypted = cipher.encrypt(pad(password.encode("utf-8"), DES.block_size))
    return base64.b64encode(encrypted).decode("ascii")


def _page_indicators(html: str) -> dict[str, Any]:
    lower = html.lower()
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""

    has_login = (
        "login_username" in lower
        or "id=\"login_form\"" in lower
        or "method=login" in lower and "login_password" in lower
    )
    has_form = any(
        [
            "collaboration" in lower and "newcoll" in lower,
            "formmain" in lower,
            "formson" in lower,
            "ctpform" in lower,
            "affair" in lower and "summary" in lower,
            "cap4" in lower,
            bool(soup.select("input[name], textarea[name], select[name]")),
        ]
    )
    return {"title": title, "has_login_page": has_login, "has_form_content": has_form}


class SeeyonClient:
    """致远 OA HTTP 客户端（Cookie 会话）。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.seeyon_root = f"{self.base_url}/seeyon"
        self.username = username
        self.password = password
        self.timeout = timeout
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SeeyonClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._client.cookies)

    def _get_login_page(self) -> tuple[str, str]:
        resp = self._client.get(f"{self.seeyon_root}/main.do")
        resp.raise_for_status()
        seed = _extract_security_seed(resp.text)
        if not seed:
            raise SeeyonLoginError("登录页未找到 _SecuritySeed，无法加密密码")
        return resp.text, seed

    def login(self) -> str:
        """登录 OA，成功返回服务器响应文本（通常以 ok 开头）。"""
        _, security_seed = self._get_login_page()
        encrypted_password = encrypt_login_password(self.password, security_seed)

        payload = {
            "login_username": self.username,
            "login_password": encrypted_password,
            "login_validatePwdStrength": "4",
            "fontSize": "12",
            "screenWidth": "1920",
            "screenHeight": "1080",
            "random": "",
            "trustdo_type": "",
            "authorization": "",
            "login.timezone": "",
            "province": "",
            "city": "",
            "redirect_url": "",
            "token_login_app_id": "",
            "token_login_third_user_id": "",
            "request_auth_authenticator": "",
            "rectangle": "",
        }

        resp = self._client.post(
            f"{self.seeyon_root}/main.do?method=login",
            data=payload,
            headers={"Accept": "text/plain", "Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        body = resp.text.strip()

        if body.startswith("ok"):
            return body

        # 部分版本返回 HTML 错误页
        if "login_username" in body and "login_password" in body:
            raise SeeyonLoginError("登录失败：账号或密码错误，或密码加密不匹配")

        snippet = body[:300].replace("\n", " ")
        raise SeeyonLoginError(f"登录失败：{snippet}")

    def fetch_template(self, template_url: str) -> TemplateAccessResult:
        """访问指定协同表单模板 URL。"""
        resp = self._client.get(template_url)
        html = resp.text
        indicators = _page_indicators(html)

        success = resp.status_code == 200 and indicators["has_form_content"] and not indicators["has_login_page"]
        error_message = ""
        if indicators["has_login_page"]:
            error_message = "页面仍为登录页，会话可能未生效"
        elif not indicators["has_form_content"]:
            error_message = "未检测到表单字段或协同模板特征"

        snippet = BeautifulSoup(html, "lxml").get_text("\n", strip=True)[:500]

        return TemplateAccessResult(
            success=success,
            status_code=resp.status_code,
            final_url=str(resp.url),
            title=indicators["title"],
            has_login_page=indicators["has_login_page"],
            has_form_content=indicators["has_form_content"],
            error_message=error_message,
            snippet=snippet,
            cookies=self.cookies,
        )

    def probe_template(self, template_url: str | None = None) -> TemplateAccessResult:
        """登录并访问模板页（一站式探测）。"""
        self.login()
        url = template_url or f"{self.seeyon_root}/main.do"
        return self.fetch_template(url)
