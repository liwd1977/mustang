"""神笔双环境与板块 yydl 映射测试。"""

from __future__ import annotations

from core.config.settings import Settings
from core.config.shenbi_environments import (
    get_shenbi_config,
    parse_environment_switch_command,
    resolve_sector_yydl,
)
from core.workflow.shenbi_builder import _default_role_person_entry


def test_resolve_sector_yydl_mapping():
    assert resolve_sector_yydl("野马集团（二线）", sector_index=2) == "2"
    assert resolve_sector_yydl("外贸板块", sector_index=3) == "3"
    assert resolve_sector_yydl("布尔津野马矿业", sector_index=9) == "9"
    assert resolve_sector_yydl("野马智科", sector_index=10) == "10"


def test_environment_profiles_role_codes():
    dev_cfg = get_shenbi_config(Settings(shenbi_environment="dev"))
    ym_cfg = get_shenbi_config(Settings(shenbi_environment="ym"))
    assert dev_cfg.admin_role_code == "admin"
    assert ym_cfg.admin_role_code == "YMADMIN"
    assert dev_cfg.tenant_id == "aa4f8c3fa79111eda3250242ac120002"
    assert ym_cfg.tenant_id == "8c12892417855bcc6b844984a4f940b9"
    assert "cloudschool" in dev_cfg.base_url
    assert "ym.zhiduo.net" in ym_cfg.base_url


def test_default_role_person_uses_environment_role():
    dev_entry = _default_role_person_entry("t1", settings=Settings(shenbi_environment="dev"))
    ym_entry = _default_role_person_entry("t2", settings=Settings(shenbi_environment="ym"))
    assert '"roleCode":"admin"' in dev_entry["roleInfo"]
    assert '"roleCode":"YMADMIN"' in ym_entry["roleInfo"]


def test_parse_environment_switch_command():
    assert parse_environment_switch_command("切换到开发环境") == "dev"
    assert parse_environment_switch_command("请切换野马数智化平台") == "ym"
    assert parse_environment_switch_command("hello") is None
