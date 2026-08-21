"""神笔平台多工作环境配置与板块应用分类（yydl）映射。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.settings import Settings

SHEET_SUFFIX_RE = re.compile(r"[（(][^）)]*[）)]")


@dataclass(frozen=True)
class ShenbiEnvironmentProfile:
    key: str
    label: str
    base_url: str
    origin: str
    referer: str
    default_token: str
    app_icon: str
    admin_role_code: str
    admin_role_id: str
    admin_role_name: str = "管理员"
    tenant_id: str = ""


@dataclass(frozen=True)
class ShenbiConfig:
    environment: str
    label: str
    base_url: str
    origin: str
    referer: str
    api_token: str
    app_icon: str
    admin_role_code: str
    admin_role_id: str
    admin_role_name: str
    tenant_id: str


SHENBI_PROFILES: dict[str, ShenbiEnvironmentProfile] = {
    "dev": ShenbiEnvironmentProfile(
        key="dev",
        label="开发环境",
        base_url="https://www.cloudschool.cn/gateway",
        origin="https://www.cloudschool.cn",
        referer="https://www.cloudschool.cn/baidaForm/",
        default_token="qhGhcPqf7H6xQI2u",
        app_icon=(
            "https://www.cloudschool.cn/fastdfs/group1/M00/00/1B/"
            "rBMqYmdQDQmAOkrRAAAKAl_aFqI729.png"
        ),
        admin_role_code="admin",
        admin_role_id="cc2497b91c7dd2f72668cf86447b03e6",
        tenant_id="aa4f8c3fa79111eda3250242ac120002",
    ),
    "ym": ShenbiEnvironmentProfile(
        key="ym",
        label="野马数智化平台",
        base_url="http://ym.zhiduo.net/gateway",
        origin="http://ym.zhiduo.net",
        referer="http://ym.zhiduo.net/baidaForm/",
        default_token="V4rodLnifNkzcE3b",
        app_icon=(
            "http://ym.zhiduo.net/fastdfs/group1/M00/00/00/"
            "rBQAAWppvUWAWBAKAAAKAl_aFqI819.png"
        ),
        admin_role_code="YMADMIN",
        admin_role_id="YMADMIN",
        admin_role_name="野马集团管理员",
        tenant_id="8c12892417855bcc6b844984a4f940b9",
    ),
}

SHENBI_ENVIRONMENT_OPTIONS: dict[str, str] = {
    key: profile.label for key, profile in SHENBI_PROFILES.items()
}

# 办事指南 / 任务表板块名 → 神笔应用分类 yydl（与指南第二~十板块序号一致）
SECTOR_YYDL_BY_KEYWORD: list[tuple[str, str]] = [
    ("布尔津野马矿业", "9"),
    ("布尔津矿业", "9"),
    ("野马智科", "10"),
    ("麻赛伟业", "8"),
    ("鼎泰伟业", "8"),
    ("晨城立通", "7"),
    ("晟诚立通", "7"),
    ("腾宇通达", "6"),
    ("金融板块", "5"),
    ("金融", "5"),
    ("文旅集团", "4"),
    ("文旅板块", "4"),
    ("文旅", "4"),
    ("外贸板块", "3"),
    ("外贸", "3"),
    ("野马集团", "2"),
]

ENV_SWITCH_COMMANDS: dict[str, str] = {
    "开发环境": "dev",
    "开发": "dev",
    "cloudschool": "dev",
    "野马数智化平台": "ym",
    "野马数智化": "ym",
    "数智化平台": "ym",
    "ym.zhiduo.net": "ym",
}


def normalize_sector_title(sector_title: str) -> str:
    text = SHEET_SUFFIX_RE.sub("", (sector_title or "").strip())
    text = text.replace("（二线）", "").replace("(二线)", "")
    return text.strip()


def resolve_sector_yydl(sector_title: str, *, sector_index: int = 0) -> str:
    """按办事指南板块解析应用分类 yydl。"""
    normalized = normalize_sector_title(sector_title)
    for keyword, yydl in SECTOR_YYDL_BY_KEYWORD:
        if keyword in normalized:
            return yydl
    if sector_index >= 2:
        return str(sector_index)
    return "2"


def get_shenbi_profile(env_key: str) -> ShenbiEnvironmentProfile:
    key = (env_key or "ym").strip().lower()
    return SHENBI_PROFILES.get(key, SHENBI_PROFILES["ym"])


def get_shenbi_config(settings: Settings) -> ShenbiConfig:
    """解析当前激活的神笔工作环境（含 token / Origin / 角色）。"""
    profile = get_shenbi_profile(settings.shenbi_environment)
    token_override = (settings.shenbi_api_token or "").strip()
    if profile.key == "dev":
        env_token = (settings.shenbi_dev_api_token or profile.default_token).strip()
        env_tenant = (settings.shenbi_dev_tenant_id or profile.tenant_id).strip()
    else:
        env_token = (settings.shenbi_ym_api_token or profile.default_token).strip()
        env_tenant = (settings.shenbi_ym_tenant_id or profile.tenant_id).strip()
    api_token = token_override or env_token
    tenant_override = (settings.shenbi_tenant_id or "").strip()
    other_tenant_ids = {
        p.tenant_id.strip()
        for p in SHENBI_PROFILES.values()
        if p.key != profile.key and p.tenant_id
    }
    if tenant_override and tenant_override != env_tenant:
        if tenant_override in other_tenant_ids:
            tenant_id = env_tenant
        else:
            tenant_id = tenant_override
    else:
        tenant_id = env_tenant
    base_override = (settings.shenbi_base_url or "").strip()
    other_base_urls = {
        p.base_url.rstrip("/")
        for p in SHENBI_PROFILES.values()
        if p.key != profile.key
    }
    profile_base = profile.base_url.rstrip("/")
    if base_override and base_override.rstrip("/") != profile_base:
        if base_override.rstrip("/") in other_base_urls:
            base_url = profile_base
        else:
            base_url = base_override.rstrip("/")
    else:
        base_url = profile_base
    return ShenbiConfig(
        environment=profile.key,
        label=profile.label,
        base_url=base_url.rstrip("/"),
        origin=profile.origin,
        referer=profile.referer,
        api_token=api_token,
        app_icon=profile.app_icon,
        admin_role_code=profile.admin_role_code,
        admin_role_id=profile.admin_role_id,
        admin_role_name=profile.admin_role_name,
        tenant_id=tenant_id,
    )


def resolve_shenbi_tenant_id(settings: Settings) -> str:
    """当前神笔环境应使用的 tenantId（与 ym/dev 配置一致）。"""
    return get_shenbi_config(settings).tenant_id


def parse_environment_switch_command(text: str) -> str | None:
    """解析「切换到开发环境 / 野马数智化平台」等切换指令。"""
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if "切换" not in raw and "switch" not in lowered:
        return None
    for phrase, env_key in ENV_SWITCH_COMMANDS.items():
        if phrase.lower() in lowered or phrase in raw:
            return env_key
    if "dev" in lowered:
        return "dev"
    if "ym" in lowered or "zhiduo" in lowered:
        return "ym"
    return None
