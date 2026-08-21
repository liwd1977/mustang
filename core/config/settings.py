"""项目配置 — 参考 Consult_Claude 的 dotenv 加载方式。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _ensure_dotenv_loaded() -> None:
    """加载 .env（override=False），系统环境变量优先。参考 Consult_Claude。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception:
        pass


def resolve_llm_api_key() -> str:
    """VLM 与文本 LLM 共用 DashScope Key，支持多种环境变量名。"""
    _ensure_dotenv_loaded()
    placeholders = {"", "your_dashscope_api_key_here"}
    for name in ("QWEN_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value not in placeholders:
            return value
    return ""


def resolve_openai_api_key() -> str:
    return resolve_llm_api_key()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    oa_base_url: str = "http://117.190.14.58:8899"
    oa_username: str = ""
    oa_password: str = ""
    oa_template_url: str = (
        "http://117.190.14.58:8899/seeyon/collaboration/collaboration.do"
        "?method=newColl&rescode=F01_newColl&showTab=true"
        "&recommendMenuId=-4140425781984149261&menuSummary=add&portalId=1"
        "&_resourceCode=F01_newColl"
    )
    oa_playwright_timeout_ms: int = 120_000
    oa_proxy: str = Field(default="", description="访问 OA 的 HTTP 代理，如 http://127.0.0.1:7897")
    openai_api_key: str = ""
    qwen_api_key: str = ""
    default_llm_provider: str = "qwen"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_request_timeout: float = 180.0
    vlm_model_name: str = "qwen-vl-max"
    text_model_name: str = "qwen-plus"
    data_dir: Path = Path("./data")
    output_dir: Path = Path("./output")
    log_dir: Path = Path("./logs")
    guide_docx_name: str = "野马集团办事指南20260816.docx"
    guide_output_stem: str = "guide_20260816"
    flow_task_table_name: str = "野马集团流程任务表0816.xlsx"
    template_table_name: str = "可做模板表0812.xlsx"
    vlm_flow_limit: int = 0
    guide_parse_use_cache: bool = Field(
        default=False,
        description=(
            "False=开发试验：写入前 Word 有更新则自动重新解析，且单次解析不复用 VLM 流程缓存；"
            "True=批量模式：复用 parse 缓存加速，文档更新后请在流程解析页手动重新解析"
        ),
    )
    workflow_per_sector_limit: int = Field(
        default=0,
        description="全量试点：每板块除静态样例外仅解析/写入前 N 个流程；0=不限制",
    )

    shenbi_environment: str = Field(
        default="ym",
        description="神笔工作环境：dev=开发环境(cloudschool)，ym=野马数智化平台",
    )
    shenbi_dev_api_token: str = "qhGhcPqf7H6xQI2u"
    shenbi_ym_api_token: str = "V4rodLnifNkzcE3b"
    shenbi_dev_tenant_id: str = "aa4f8c3fa79111eda3250242ac120002"
    shenbi_ym_tenant_id: str = "8c12892417855bcc6b844984a4f940b9"
    shenbi_base_url: str = "http://ym.zhiduo.net/gateway"
    shenbi_api_token: str = ""
    shenbi_ssl_verify: bool = False
    shenbi_session_cookie: str = Field(
        default="SESSION=SESSION",
        description="神笔平台浏览器 Cookie，可从 DevTools 复制 SESSION 值",
    )
    shenbi_proxy: str = Field(
        default="",
        description="访问神笔平台的 HTTP 代理；留空则回退到 OA_PROXY",
    )
    shenbi_app_id: str = "bf610a25d83c6bf843a8893073a3b304"
    shenbi_tenant_id: str = ""

    @property
    def flow_task_table_path(self) -> Path:
        return self.data_dir / self.flow_task_table_name

    @property
    def template_table_path(self) -> Path:
        return self.data_dir / self.template_table_name

    @property
    def forms_store_dir(self) -> Path:
        return self.output_dir / "forms"

    @property
    def seeyon_root(self) -> str:
        return self.oa_base_url.rstrip("/") + "/seeyon"

    @property
    def guide_docx_path(self) -> Path:
        return self.data_dir / self.guide_docx_name

    @property
    def resolved_openai_api_key(self) -> str:
        return resolve_llm_api_key() or self.qwen_api_key or self.openai_api_key

    @property
    def llm_configured(self) -> bool:
        key = self.resolved_openai_api_key
        return bool(key and key != "your_dashscope_api_key_here")


@lru_cache
def get_settings() -> Settings:
    _ensure_dotenv_loaded()
    return _build_settings()


def reload_settings() -> Settings:
    """重新加载配置（Streamlit 热重载后或 .env 变更时调用）。"""
    get_settings.cache_clear()
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        try:
            from dotenv import load_dotenv

            # override=True：外部修改 .env 后（如 Cursor）立即覆盖进程内旧值
            load_dotenv(env_path, override=True)
        except Exception:
            pass
    return get_settings()


def env_file_path() -> Path:
    return PROJECT_ROOT / ".env"


def env_file_mtime() -> float | None:
    path = env_file_path()
    return path.stat().st_mtime if path.is_file() else None


def update_env_var(name: str, value: str) -> Path:
    """更新 .env 中的配置项并刷新内存缓存（供 Cursor / 脚本改 token 后即时生效）。"""
    path = env_file_path()
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()

    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        key, _, _rest = line.partition("=")
        if key.strip() == name:
            new_lines.append(f"{name}={value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{name}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    reload_settings()
    return path


def update_shenbi_token(token: str) -> Path:
    """更新神笔 fighter-auth-token 并立即刷新配置。"""
    return update_env_var("SHENBI_API_TOKEN", token.strip())


def switch_shenbi_environment(env_key: str) -> Path:
    """切换神笔工作环境并同步 .env 中的 base_url / token。"""
    from core.config.shenbi_environments import get_shenbi_profile

    profile = get_shenbi_profile(env_key)
    current = get_settings()
    if profile.key == "dev":
        token = (current.shenbi_dev_api_token or profile.default_token).strip()
        tenant = (current.shenbi_dev_tenant_id or profile.tenant_id).strip()
    else:
        token = (current.shenbi_ym_api_token or profile.default_token).strip()
        tenant = (current.shenbi_ym_tenant_id or profile.tenant_id).strip()
    update_env_var("SHENBI_ENVIRONMENT", profile.key)
    update_env_var("SHENBI_BASE_URL", profile.base_url)
    update_env_var("SHENBI_API_TOKEN", token)
    update_env_var("SHENBI_TENANT_ID", tenant)
    reload_settings()
    return env_file_path()


def _build_settings() -> Settings:
    settings = Settings()
    if not settings.output_dir.is_absolute():
        settings.output_dir = (PROJECT_ROOT / settings.output_dir).resolve()
    if not settings.data_dir.is_absolute():
        settings.data_dir = (PROJECT_ROOT / settings.data_dir).resolve()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not settings.log_dir.is_absolute():
        settings.log_dir = (PROJECT_ROOT / settings.log_dir).resolve()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.forms_store_dir.mkdir(parents=True, exist_ok=True)
    from core.config.shenbi_environments import get_shenbi_config

    cfg = get_shenbi_config(settings)
    settings.shenbi_tenant_id = cfg.tenant_id
    if not (settings.shenbi_api_token or "").strip():
        settings.shenbi_api_token = cfg.api_token
    return settings
