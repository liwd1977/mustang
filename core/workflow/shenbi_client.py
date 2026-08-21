"""神笔平台流程保存 API 客户端。"""

from __future__ import annotations

import base64
import copy
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from core.config.settings import PROJECT_ROOT, Settings, get_settings
from core.config.shenbi_environments import get_shenbi_config, resolve_sector_yydl
from core.workflow.flow_topology import (
    local_task_tree_intact,
    min_route_branches_for_local,
    task_tree_degraded,
    task_tree_has_routes,
)
from core.workflow.shenbi_builder import finalize_workflow_model, workflow_registry_slug

MODEL_SAVE_PATH = "/fighter-baida/api/flow-simple/proc/model-save"
APP_SAVE_PATH = "/fighter-baida/api/baida/tBaidaApp/saveOrUpdate"
APP_QUERY_LIST_PATH = "/fighter-baida/api/baida/tBaidaApp/queryList"
APP_MENU_SAVE_PATH = "/fighter-baida/api/baida/tBaidaMenu/saveOrUpdate"
APP_MENU_QUERY_PATH = "/fighter-baida/api/baida/tBaidaMenu/queryList"
PUBLISH_PATH = "/fighter-baida/api/flow-simple/proc/publish"
PUBLISH_ALT_PATHS = (
    "/fighter-baida/api/flow-simple/proc/deploy",
    "/fighter-baida/api/flow-simple/proc/releaseProc",
    "/fighter-baida/api/flow-simple/proc/publishProc",
    "/fighter-baida/api/flow-simple/proc/model-publish",
    "/fighter-baida/api/flow-simple/proc/saveAndPublish",
)
DEFAULT_FLOW_MENU_ICON = "el-icon-_condition"
FLOW_MENU_TYPE = 2  # 流程表单页（参考 L_测试3 → 测试流程3）
MODEL_FIELDS = ("id", "formModelJson", "formModel", "wfSimpleProc", "wfSimpleTaskInfo")
SUCCESS_CODES = {None, 0, 200, "0", "200", "success"}
SHENBI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def _resolve_proxy(settings: Settings) -> str | None:
    proxy = (settings.shenbi_proxy or settings.oa_proxy or "").strip()
    return proxy or None


def _resolve_token(token: str | None, settings: Settings) -> str:
    explicit = (token or "").strip()
    if explicit:
        return explicit
    return get_shenbi_config(settings).api_token


def _build_request_cookie(token: str, settings: Settings) -> str:
    """合并浏览器 SESSION 与 fighter-auth-token（网关发布/运行时查询常需 Cookie 中带 token）。"""
    raw = (settings.shenbi_session_cookie or "").strip()
    parts: list[str] = []
    normalized = raw.replace(" ", "")
    if raw and normalized not in {"SESSION=SESSION", "SESSION"}:
        parts.append(raw.rstrip("; "))
    token_cookie = f"fighter-auth-token={token}"
    if not any(p.strip().startswith("fighter-auth-token=") for p in parts):
        parts.append(token_cookie)
    return "; ".join(parts)


def _api_headers(token: str, *, settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    cfg = get_shenbi_config(settings)
    headers = {
        "fighter-auth-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": cfg.origin,
        "Referer": cfg.referer,
        "User-Agent": SHENBI_USER_AGENT,
    }
    tenant_id = (cfg.tenant_id or settings.shenbi_tenant_id or "").strip()
    if tenant_id:
        headers["tenantId"] = tenant_id
        headers["Tenant-Id"] = tenant_id
    headers["Cookie"] = _build_request_cookie(token, settings)
    return headers


def _http_client(*, settings: Settings, timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        verify=settings.shenbi_ssl_verify,
        proxy=_resolve_proxy(settings),
    )


def _explain_api_code(body: dict) -> str:
    code = body.get("code")
    msg = body.get("msg") or body.get("message")
    if code in (401, "401"):
        return "登录信息失效，请重新登录神笔平台并更新 fighter-auth-token。"
    if code in (400, "400") and msg:
        return str(msg)
    if code in (99999, "99999") and not msg:
        return (
            "神笔平台返回通用错误 99999（无详细说明）。"
            "请确认 wfSimpleDefaultModel 包装格式、token 与 SESSION Cookie 是否来自同一会话。"
        )
    return str(msg or body)


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_field(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    text = value.strip()
    if text.startswith(("{", "[")):
        try:
            return json.loads(text)
        except Exception:
            return value
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
    except Exception:
        return value
    if decoded.startswith(("{", "[")):
        try:
            return json.loads(decoded)
        except Exception:
            return decoded
    return value


def _model_inner_dict(model: dict) -> dict:
    return {key: model.get(key) for key in MODEL_FIELDS}


def sync_form_model_json(model: dict) -> dict:
    """保存前将 formModel 同步到 formModelJson，平台设计器主要读取后者。"""
    updated = copy.deepcopy(model)
    form_model = updated.get("formModel")
    if isinstance(form_model, dict):
        updated["formModelJson"] = _compact_json(form_model)
    return updated


def prepare_save_payload(model: dict) -> dict:
    """浏览器 model-save：整包模型 JSON → Base64 → wfSimpleDefaultModel。"""
    synced = sync_form_model_json(model)
    raw = _compact_json(_model_inner_dict(synced)).encode("utf-8")
    return {"wfSimpleDefaultModel": base64.b64encode(raw).decode("ascii")}


def serialize_save_payload(model: dict) -> bytes:
    return _compact_json(prepare_save_payload(model)).encode("utf-8")


from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def _generate_table_name() -> str:
    """新建流程：客户端生成表名（须非空；格式参考 cslc2_807148）。"""
    return f"cslc_{uuid.uuid4().hex[:6]}"


def strip_task_create_ids(task_root: dict | None) -> None:
    """新建复杂流程：环节 id/pid 及 properties 内引用须为空，平台才接受 ROUTE 树。"""
    if not isinstance(task_root, dict):
        return
    for task in iter_all_tasks(task_root):
        task["id"] = None
        task["procId"] = None
        task["pid"] = None
        props = task.get("properties")
        if not isinstance(props, dict):
            continue
        for fp in props.get("fromPropertyList") or []:
            if isinstance(fp, dict):
                fp["id"] = None
                fp["taskId"] = None
        for btn in props.get("buttonList") or []:
            if isinstance(btn, dict):
                btn["id"] = None
                btn["taskId"] = None
        for bc in props.get("branchConditionList") or []:
            if isinstance(bc, dict):
                bc["id"] = None
                bc["taskId"] = None
        for pl in props.get("personList") or []:
            if isinstance(pl, dict):
                pl["id"] = None
                pl["taskId"] = None


def index_tasks_by_key(task_root: dict | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(task_root, dict):
        return out
    for task in iter_all_tasks(task_root):
        key = str(task.get("taskKey") or "").strip()
        if key:
            out[key] = task
    return out


def sync_from_property_list_task_ids(task_root: dict | None) -> None:
    """fromPropertyList.taskId 与环节 id 对齐（权限绑定必需）。"""
    if not isinstance(task_root, dict):
        return
    for task in iter_all_tasks(task_root):
        props = task.get("properties")
        if not isinstance(props, dict):
            continue
        task_id = task.get("id")
        for fp in props.get("fromPropertyList") or []:
            if isinstance(fp, dict):
                fp["taskId"] = task_id if task_id else None


def prepare_from_property_list_for_platform_save(task_root: dict | None) -> None:
    """权限 update 保存前：对齐 taskId、清空条目 id，并去掉系统隐藏字段的 hide 项。"""
    from core.workflow.shenbi_builder import HIDDEN_FORM_FIELD_PROPS

    if not isinstance(task_root, dict):
        return
    sync_from_property_list_task_ids(task_root)
    for task in iter_all_tasks(task_root):
        if task.get("type") not in {"STARTTASK", "USERTASK"}:
            continue
        props = task.get("properties")
        if not isinstance(props, dict):
            continue
        fpl = props.get("fromPropertyList")
        if not isinstance(fpl, list):
            continue
        cleaned: list[dict] = []
        for fp in fpl:
            if not isinstance(fp, dict):
                continue
            prop = fp.get("fieldProp")
            if fp.get("operating") == "hide" and prop in HIDDEN_FORM_FIELD_PROPS:
                continue
            entry = dict(fp)
            entry["id"] = None
            entry["creator"] = None
            entry["createTime"] = None
            entry["updator"] = None
            entry["updateTime"] = None
            cleaned.append(entry)
        props["fromPropertyList"] = cleaned


def merge_local_form_permissions_by_task_key(
    platform_root: dict | None,
    local_root: dict | None,
) -> None:
    """仅把本地 fromPropertyList 按 taskKey 覆盖到平台环节树，保留平台 pid/procId/按钮/办理人。"""
    if not isinstance(platform_root, dict) or not isinstance(local_root, dict):
        return
    local_by_key = index_tasks_by_key(local_root)
    for task in iter_all_tasks(platform_root):
        if task.get("type") not in {"STARTTASK", "USERTASK"}:
            continue
        key = str(task.get("taskKey") or "").strip()
        local_task = local_by_key.get(key)
        if not local_task:
            continue
        local_props = local_task.get("properties") if isinstance(local_task.get("properties"), dict) else {}
        local_fpl = local_props.get("fromPropertyList")
        if not isinstance(local_fpl, list):
            continue
        props = task.setdefault("properties", {})
        if not isinstance(props, dict):
            continue
        task_id = task.get("id")
        merged_fpl: list[dict] = []
        for fp in local_fpl:
            if not isinstance(fp, dict):
                continue
            entry = copy.deepcopy(fp)
            entry["id"] = None
            entry["taskId"] = str(task_id) if task_id else None
            entry["creator"] = None
            entry["createTime"] = None
            entry["updator"] = None
            entry["updateTime"] = None
            merged_fpl.append(entry)
        props["fromPropertyList"] = merged_fpl


def _merge_property_row_ids(local_rows: Any, plat_rows: Any) -> None:
    if not isinstance(local_rows, list) or not isinstance(plat_rows, list):
        return
    for i, local_row in enumerate(local_rows):
        if not isinstance(local_row, dict) or i >= len(plat_rows):
            continue
        plat_row = plat_rows[i]
        if not isinstance(plat_row, dict):
            continue
        if plat_row.get("id") is not None:
            local_row["id"] = plat_row["id"]
        if plat_row.get("taskId") is not None:
            local_row["taskId"] = plat_row["taskId"]


def merge_platform_task_tree_inplace(local_node: dict | None, plat_node: dict | None) -> None:
    """按相同拓扑位置合并平台 id/pid/procId（含 ROUTE 等无 taskKey 节点）。"""
    if not isinstance(local_node, dict):
        return
    if isinstance(plat_node, dict):
        for field in ("id", "pid", "procId"):
            if plat_node.get(field) is not None:
                local_node[field] = plat_node[field]
        local_props = local_node.get("properties")
        plat_props = plat_node.get("properties")
        if isinstance(local_props, dict) and isinstance(plat_props, dict):
            for key in ("buttonList", "branchConditionList", "personList"):
                _merge_property_row_ids(local_props.get(key), plat_props.get(key))

    local_child = local_node.get("child")
    plat_child = plat_node.get("child") if isinstance(plat_node, dict) else None
    if isinstance(local_child, dict):
        merge_platform_task_tree_inplace(local_child, plat_child if isinstance(plat_child, dict) else None)

    local_conds = local_node.get("conditions") or []
    plat_conds = (plat_node.get("conditions") or []) if isinstance(plat_node, dict) else []
    if isinstance(local_conds, list):
        for idx, local_cond in enumerate(local_conds):
            if not isinstance(local_cond, dict):
                continue
            plat_cond = plat_conds[idx] if idx < len(plat_conds) else None
            merge_platform_task_tree_inplace(local_cond, plat_cond if isinstance(plat_cond, dict) else None)


def task_tree_from_save_response(response: dict | None) -> dict | None:
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    task = _maybe_parse_json(data.get("wfSimpleTaskInfo"))
    return task if isinstance(task, dict) and task.get("type") else None


def _start_task_has_platform_id(task_root: dict | None) -> bool:
    if not isinstance(task_root, dict):
        return False
    return bool(task_root.get("id"))


def merge_platform_task_ids(local_root: dict | None, platform_root: dict | None) -> None:
    """按 taskKey 将平台环节 id 写回本地树（保留本地 fromPropertyList）。"""
    if not isinstance(local_root, dict) or not isinstance(platform_root, dict):
        return
    plat = index_tasks_by_key(platform_root)
    for task in iter_all_tasks(local_root):
        key = str(task.get("taskKey") or "").strip()
        if not key:
            continue
        plat_task = plat.get(key)
        if not plat_task:
            continue
        plat_id = plat_task.get("id")
        if plat_id:
            task["id"] = plat_id
    sync_from_property_list_task_ids(local_root)


def extract_task_id_by_key(task_root: dict | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not isinstance(task_root, dict):
        return mapping
    for task in iter_all_tasks(task_root):
        key = str(task.get("taskKey") or "").strip()
        tid = task.get("id")
        if key and tid:
            mapping[key] = str(tid)
    return mapping


def apply_task_id_by_key(task_root: dict | None, mapping: dict[str, str] | None) -> None:
    if not isinstance(task_root, dict) or not mapping:
        return
    for task in iter_all_tasks(task_root):
        key = str(task.get("taskKey") or "").strip()
        if key and key in mapping:
            task["id"] = mapping[key]
    sync_from_property_list_task_ids(task_root)


def strip_create_ids(model: dict) -> dict:
    """新建流程：剥离平台 ID；保留/生成 tableName（model-save 必填）。"""
    stripped = copy.deepcopy(model)
    stripped["id"] = None
    stripped["formModelJson"] = None
    form_model = stripped.get("formModel")
    if isinstance(form_model, dict):
        form_model.pop("formId", None)
        form_model["tableName"] = _generate_table_name()
    proc = stripped.get("wfSimpleProc")
    if isinstance(proc, dict):
        proc.pop("fromId", None)
        proc.pop("procKey", None)
        proc["id"] = None
        if isinstance(form_model, dict) and form_model.get("tableName"):
            # 新建时 procKey 与 tableName 保持一致（样例规律）
            proc["procKey"] = form_model["tableName"]
    strip_task_create_ids(stripped.get("wfSimpleTaskInfo"))
    return stripped


def apply_saved_ids(model: dict, *, form_id: str, proc_id: str) -> dict:
    """将平台返回的 formId / procId 写回模型，并同步流程环节树（保留设计器节点 id）。"""
    from core.workflow.shenbi_builder import _sync_task_tree_proc_ids

    updated = apply_proc_form_ids_only(model, form_id=form_id, proc_id=proc_id)
    if updated.get("wfSimpleTaskInfo"):
        updated["wfSimpleTaskInfo"] = _sync_task_tree_proc_ids(
            updated["wfSimpleTaskInfo"], proc_id=proc_id
        )
    return updated


def apply_proc_form_ids_only(model: dict, *, form_id: str, proc_id: str) -> dict:
    """仅写入 formId/procId，不重建环节 id/pid（权限补写须保留 create 后平台 id 链）。"""
    updated = copy.deepcopy(model)
    form_model = updated.setdefault("formModel", {})
    proc = updated.setdefault("wfSimpleProc", {})
    form_model["formId"] = form_id
    proc["fromId"] = form_id
    proc["id"] = proc_id
    proc["complexProcId"] = None
    return updated


def workflow_registry_path(workflow_name: str, *, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    slug = workflow_registry_slug(workflow_name)
    return settings.output_dir / "workflows" / "shenbi" / f"{slug}_registry.json"


def load_workflow_registry(workflow_name: str, *, settings: Settings | None = None) -> dict | None:
    """读取本地已创建流程的 appId / formId / procId（按 workflow_name 索引）。"""
    path = workflow_registry_path(workflow_name, settings=settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    form_id = data.get("form_id")
    proc_id = data.get("proc_id")
    if form_id and proc_id:
        return data
    return None


def registry_app_verified(registry: dict | None) -> bool:
    """仅当 registry 记录的应用确实由 tBaidaApp/saveOrUpdate 创建时为 True。"""
    return bool(registry and registry.get("app_created_via_api"))


def is_legacy_registry(registry: dict | None, *, settings: Settings | None = None) -> bool:
    """旧版写入挂 .env 默认 appId，未在「我的应用」中创建独立应用。"""
    if not registry:
        return False
    if registry_app_verified(registry):
        return False
    settings = settings or get_settings()
    app_id = str(registry.get("app_id") or "").strip()
    default_app = (settings.shenbi_app_id or "").strip()
    return bool(app_id) and app_id == default_app


def save_workflow_registry(
    workflow_name: str,
    *,
    app_id: str,
    app_mc: str | None = None,
    form_id: str,
    proc_id: str,
    proc_key: str,
    table_name: str | None = None,
    task_id_by_key: dict[str, str] | None = None,
    settings: Settings | None = None,
) -> Path:
    settings = settings or get_settings()
    path = workflow_registry_path(workflow_name, settings=settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    from core.workflow.shenbi_builder import _now_str, display_workflow_name

    payload: dict[str, Any] = {
        "workflow_name": workflow_name,
        "app_id": app_id,
        "app_mc": app_mc or display_workflow_name(workflow_name),
        "proc_key": proc_key,
        "form_id": form_id,
        "proc_id": proc_id,
        "table_name": table_name or proc_key,
        "app_created_via_api": True,
        "updated_at": _now_str(),
    }
    if task_id_by_key:
        payload["task_id_by_key"] = task_id_by_key
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def build_app_save_payload(
    workflow_name: str,
    *,
    app_id: str | None = None,
    app_mc: str | None = None,
    form_slug: str | None = None,
    app_category: str | None = None,
    sector_title: str | None = None,
    sector_index: int = 0,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """组装 tBaidaApp/saveOrUpdate 请求体；mc = 流程显示名。"""
    from core.workflow.shenbi_builder import display_workflow_name

    settings = settings or get_settings()
    cfg = get_shenbi_config(settings)
    mc = app_mc or display_workflow_name(workflow_name)
    if not form_slug:
        from core.workflow.workflow_catalog import get_catalog_meta

        meta = get_catalog_meta(workflow_name, settings=settings)
        form_slug = str(meta.get("form_slug") or workflow_name)
    else:
        form_slug = str(form_slug)
    yydl = app_category or resolve_sector_yydl(sector_title or "", sector_index=sector_index)
    payload: dict[str, Any] = {
        "icons": cfg.app_icon,
        "mc": mc,
        "sfqy": "1",
        "template": 0,
        "yyjj": f"野马智能交付工作台自动创建 · 关联 OA 表单 {form_slug}",
        "yydl": yydl,
        "device": 2,
        "sfsy": 0,
    }
    if app_id:
        payload["id"] = app_id
    return payload


def extract_app_id(response: dict) -> str | None:
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("id", "appId"):
            if data.get(key):
                return str(data[key])
    if isinstance(data, str) and data.strip():
        return data.strip()
    if response.get("id"):
        return str(response["id"])
    return None


def save_or_update_app(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """调用 tBaidaApp/saveOrUpdate 创建或更新应用。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    if not token:
        raise ShenbiApiError("未配置神笔平台 token（fighter-auth-token）")

    base = settings.shenbi_base_url.rstrip("/")
    url = f"{base}{APP_SAVE_PATH}"
    headers = _api_headers(token, settings=settings)
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.ConnectError as exc:
        raise ShenbiApiError(f"无法连接神笔平台（创建应用）：{exc}") from exc

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        raise ShenbiApiError(
            f"创建应用 API 请求失败 HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )

    if isinstance(body, dict):
        code = body.get("code")
        if code not in SUCCESS_CODES:
            raise ShenbiApiError(
                f"创建应用失败（code={code}）：{_explain_api_code(body)}",
                status_code=resp.status_code,
                body=body,
            )
    return body if isinstance(body, dict) else {"raw": body}


def query_app_list(
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """查询租户下全部应用（tBaidaApp/queryList）。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    if not token:
        raise ShenbiApiError("未配置神笔平台 token（fighter-auth-token）")

    base = settings.shenbi_base_url.rstrip("/")
    url = f"{base}{APP_QUERY_LIST_PATH}"
    headers = _api_headers(token, settings=settings)
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(url, json={}, headers=headers)
    except httpx.ConnectError as exc:
        raise ShenbiApiError(f"无法连接神笔平台（查询应用列表）：{exc}") from exc

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        raise ShenbiApiError(
            f"查询应用列表失败 HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )

    if isinstance(body, dict):
        code = body.get("code")
        if code not in SUCCESS_CODES:
            raise ShenbiApiError(
                f"查询应用列表失败（code={code}）：{_explain_api_code(body)}",
                status_code=resp.status_code,
                body=body,
            )
        data = body.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def collect_app_name_candidates(
    workflow_name: str,
    registry: dict | None = None,
) -> list[str]:
    """收集应用名称候选（含 guide hints 与去掉单/表 的旧版名称）。"""
    from core.form.name_utils import display_form_name
    from core.workflow.shenbi_builder import display_workflow_name
    from core.workflow.workflow_catalog import get_catalog_hints

    names: list[str] = []

    def add(raw: str) -> None:
        text = display_form_name(str(raw or "")).strip()
        if text and text not in names:
            names.append(text)

    add(display_workflow_name(workflow_name))
    if registry:
        add(str(registry.get("app_mc") or ""))
    for hint in get_catalog_hints(workflow_name):
        add(hint)
    for name in list(names):
        for suffix in ("单", "表"):
            if name.endswith(suffix) and len(name) > len(suffix):
                add(name[: -len(suffix)])
    return names


def find_app_ids_by_name_candidates(
    candidates: list[str],
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> list[tuple[str, str]]:
    """按名称候选匹配全部应用，返回 [(app_id, mc)]（去重）。"""
    targets = {(c or "").strip() for c in candidates if (c or "").strip()}
    if not targets:
        return []
    seen: set[str] = set()
    matches: list[tuple[str, str]] = []
    for app in query_app_list(settings=settings, token=token):
        mc = (app.get("mc") or "").strip()
        app_id = app.get("id")
        if not mc or not app_id:
            continue
        if mc in targets:
            aid = str(app_id)
            if aid not in seen:
                seen.add(aid)
                matches.append((aid, mc))
    return matches


def find_app_id_by_mc(
    mc: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> str | None:
    """按应用名称精确匹配 appId。"""
    matches = find_app_ids_by_name_candidates([mc], settings=settings, token=token)
    return matches[0][0] if matches else None


def app_exists_on_platform(
    app_id: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> bool:
    """确认 appId 在 tBaidaApp/queryList 中真实存在（避免幽灵 id 误报成功）。"""
    aid = (app_id or "").strip()
    if not aid:
        return False
    for app in query_app_list(settings=settings, token=token):
        if str(app.get("id") or "").strip() == aid:
            return True
    return False


def resolve_app_id_for_workflow(
    workflow_name: str,
    *,
    registry: dict | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> tuple[str | None, str]:
    """解析应写入的目标应用：verified registry > 名称候选（含旧版无单/表）。"""
    registry = registry or load_workflow_registry(workflow_name, settings=settings)
    if registry_app_verified(registry) and registry.get("app_id"):
        reg_id = str(registry["app_id"])
        if app_exists_on_platform(reg_id, settings=settings, token=token):
            return reg_id, "registry"
    candidates = collect_app_name_candidates(workflow_name, registry)
    matches = find_app_ids_by_name_candidates(candidates, settings=settings, token=token)
    if matches:
        return matches[0][0], "reuse"
    return None, "create"


def rebind_flow_menus_for_workflow_aliases(
    *,
    workflow_name: str,
    primary_app_id: str,
    title: str,
    form_id: str,
    proc_id: str,
    proc_key: str,
    registry: dict | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> list[dict]:
    """将主应用及所有同名/旧名别名的应用菜单绑定到同一 formId/procId。"""
    app_ids: list[str] = []
    seen: set[str] = set()

    def add(app_id: str) -> None:
        aid = (app_id or "").strip()
        if aid and aid not in seen:
            seen.add(aid)
            app_ids.append(aid)

    add(primary_app_id)
    for aid, _mc in find_app_ids_by_name_candidates(
        collect_app_name_candidates(workflow_name, registry),
        settings=settings,
        token=token,
    ):
        add(aid)

    steps: list[dict] = []
    for aid in app_ids:
        steps.extend(
            rebind_all_flow_menus(
                app_id=aid,
                title=title,
                form_id=form_id,
                proc_id=proc_id,
                proc_key=proc_key,
                settings=settings,
                token=token,
            )
        )
    return steps


def _app_name_exists_error(exc: ShenbiApiError) -> bool:
    text = str(exc)
    body = exc.body if isinstance(exc.body, dict) else {}
    for part in (text, body.get("msg"), body.get("exceptionMsg"), body.get("message")):
        if part and "应用名称已经存在" in str(part):
            return True
    return False


def list_app_menus(
    app_id: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """返回指定应用下的页面菜单（按 applicationId 过滤）。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    if not token:
        raise ShenbiApiError("未配置神笔平台 token（fighter-auth-token）")

    base = settings.shenbi_base_url.rstrip("/")
    url = f"{base}{APP_MENU_QUERY_PATH}"
    headers = _api_headers(token, settings=settings)
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(url, json={"appId": app_id}, headers=headers)
    except httpx.ConnectError as exc:
        raise ShenbiApiError(f"无法连接神笔平台（查询应用菜单）：{exc}") from exc

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        raise ShenbiApiError(
            f"查询应用菜单失败 HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )

    if isinstance(body, dict):
        code = body.get("code")
        if code not in SUCCESS_CODES:
            raise ShenbiApiError(
                f"查询应用菜单失败（code={code}）：{_explain_api_code(body)}",
                status_code=resp.status_code,
                body=body,
            )
        data = body.get("data")
        if isinstance(data, list):
            target = str(app_id)
            return [item for item in data if isinstance(item, dict) and str(item.get("applicationId") or "") == target]
    return []


def list_flow_menus(
    app_id: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """返回应用内全部流程页菜单（menuType=2）。"""
    return [
        menu
        for menu in list_app_menus(app_id, settings=settings, token=token)
        if menu.get("menuType") == FLOW_MENU_TYPE
    ]


def _menu_bound_ids(menu: dict) -> tuple[str | None, str | None]:
    extend = menu.get("extendInfo") if isinstance(menu.get("extendInfo"), dict) else {}
    form_id = str(extend.get("trendsFlowId") or "").strip() or None
    proc_id = str(extend.get("queryId") or "").strip() or None
    return form_id, proc_id


def rebind_all_flow_menus(
    *,
    app_id: str,
    title: str,
    form_id: str,
    proc_id: str,
    proc_key: str,
    settings: Settings | None = None,
    token: str | None = None,
) -> list[dict]:
    """将应用内所有流程菜单绑定到同一 formId/procId（设计器入口与预览页一致）。"""
    steps: list[dict] = []
    menus = list_flow_menus(app_id, settings=settings, token=token)
    if not menus:
        step = ensure_flow_menu_for_app(
            app_id=app_id,
            title=title,
            form_id=form_id,
            proc_id=proc_id,
            proc_key=proc_key,
            settings=settings,
            token=token,
        )
        return [step]
    for menu in menus:
        old_title = str(menu.get("title") or "").strip()
        menu_title = title.strip() or old_title
        extend = menu.get("extendInfo") if isinstance(menu.get("extendInfo"), dict) else {}
        bound_form, bound_proc = _menu_bound_ids(menu)
        needs_rebind = bound_form != form_id or bound_proc != proc_id
        needs_rename = old_title != menu_title
        payload = build_flow_menu_payload(
            app_id=app_id,
            title=menu_title,
            form_id=form_id,
            proc_id=proc_id,
            proc_key=proc_key,
            menu_id=str(menu["menuId"]) if menu.get("menuId") else None,
            sort_number=int(menu.get("sortNumber") or 5),
        )
        if needs_rebind or needs_rename or not menu.get("menuId"):
            mode = "rebind" if menu.get("menuId") else "create"
        else:
            mode = "existing"
        response = save_app_menu(payload, settings=settings, token=token)
        steps.append(
            {
                "step": "应用流程菜单",
                "mode": mode,
                "menu_id": extract_menu_id(response) or menu.get("menuId"),
                "title": menu_title,
                "response": response,
            }
        )
    return steps


def fetch_platform_model(
    *,
    form_id: str,
    proc_id: str,
    proc_key: str,
    app_id: str,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict | None:
    """model-get 回读平台流程；失败返回 None。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    if not token:
        return None
    base = settings.shenbi_base_url.rstrip("/")
    headers = _api_headers(token, settings=settings)
    body: dict[str, Any] = {
        "formId": form_id,
        "procId": proc_id,
        "id": proc_id,
        "procKey": proc_key,
        "appId": app_id,
    }
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(
                f"{base}/fighter-baida/api/flow-simple/proc/model-get",
                json=body,
                headers=headers,
            )
            payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("code") not in SUCCESS_CODES:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or not _response_data_ok(data):
        return None
    return data


def _complex_task_tree_collapsed(
    task_root: dict | None,
    *,
    min_routes: int = 3,
    min_usertasks: int = 5,
) -> bool:
    """复杂招聘流程环节树已塌陷（仅申请填报/办理start，无并行 ROUTE）。"""
    if not isinstance(task_root, dict) or not task_root.get("type"):
        return True
    if _count_route_branches(task_root) >= min_routes:
        return False
    types = count_task_types(task_root)
    if int(types.get("USERTASK") or 0) >= min_usertasks:
        return False
    return True


def _platform_task_is_shell(task_root: dict | None, *, min_route_branches: int = 4) -> bool:
    if _complex_task_tree_collapsed(task_root, min_routes=min_route_branches):
        return True
    if not isinstance(task_root, dict):
        return False
    fake_resp = {"data": {"wfSimpleTaskInfo": task_root}}
    return _response_looks_like_shell(fake_resp, min_route_branches=min_route_branches)


def resolve_complex_write_target(
    *,
    app_id: str,
    registry: dict | None,
    settings: Settings | None = None,
    token: str | None = None,
) -> tuple[str | None, str | None, str]:
    """复杂流程写入目标：优先覆盖应用菜单绑定的 proc（即设计器所见）。"""
    settings = settings or get_settings()
    proc_key = str((registry or {}).get("proc_key") or "")
    candidates: list[tuple[str, str, str]] = []

    for menu in list_flow_menus(app_id, settings=settings, token=token):
        form_id, proc_id = _menu_bound_ids(menu)
        if form_id and proc_id:
            candidates.append(("menu", form_id, proc_id))

    if registry_app_verified(registry):
        reg_form = str(registry.get("form_id") or "")
        reg_proc = str(registry.get("proc_id") or "")
        if reg_form and reg_proc:
            candidates.append(("registry", reg_form, reg_proc))

    seen: set[tuple[str, str]] = set()
    for source, form_id, proc_id in candidates:
        key = (form_id, proc_id)
        if key in seen:
            continue
        seen.add(key)
        remote = fetch_platform_model(
            form_id=form_id,
            proc_id=proc_id,
            proc_key=proc_key,
            app_id=app_id,
            settings=settings,
            token=token,
        )
        if remote is None:
            continue
        task = _decode_field(remote.get("wfSimpleTaskInfo"))
        if _complex_task_tree_collapsed(task if isinstance(task, dict) else None):
            continue
        return form_id, proc_id, f"update_{source}"

    return None, None, "create"


def find_flow_menu(
    app_id: str,
    *,
    form_id: str,
    proc_id: str,
    title: str,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict | None:
    """查找应用内已绑定的流程菜单（menuType=2）。"""
    for menu in list_app_menus(app_id, settings=settings, token=token):
        if menu.get("menuType") != FLOW_MENU_TYPE:
            continue
        extend = menu.get("extendInfo") if isinstance(menu.get("extendInfo"), dict) else {}
        if str(extend.get("trendsFlowId") or "") == form_id:
            return menu
        if str(extend.get("queryId") or "") == proc_id:
            return menu
        if (menu.get("title") or "").strip() == title.strip():
            return menu
    return None


def build_flow_menu_payload(
    *,
    app_id: str,
    title: str,
    form_id: str,
    proc_id: str,
    proc_key: str,
    menu_id: str | None = None,
    query_id: str | None = None,
    sort_number: int = 5,
) -> dict[str, Any]:
    """组装流程页面菜单。规律（L_测试3 样例）：trendsFlowId=formId，query-id/procId=procId。"""
    query_id = query_id or proc_id
    payload: dict[str, Any] = {
        "applicationId": app_id,
        "parentId": "0",
        "title": title,
        "icon": DEFAULT_FLOW_MENU_ICON,
        "sortNumber": sort_number,
        "menuType": FLOW_MENU_TYPE,
        "hide": 0,
        "device": 2,
        "extendInfo": {
            "formControlParams": [],
            "modelDb": "",
            "modelType": "",
            "resourceExtList": [
                {"extPropertyValue": "flow", "extPropertyKey": "type", "description": "分类"},
                {"extPropertyValue": query_id, "extPropertyKey": "query-id", "description": "参数id"},
            ],
            "trendsFlowId": form_id,
            "queryId": query_id,
            "tableName": f"F_{proc_key}",
        },
    }
    if menu_id:
        payload["menuId"] = menu_id
    return payload


def save_app_menu(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """调用 tBaidaMenu/saveOrUpdate 创建或更新应用内流程菜单。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    if not token:
        raise ShenbiApiError("未配置神笔平台 token（fighter-auth-token）")

    base = settings.shenbi_base_url.rstrip("/")
    url = f"{base}{APP_MENU_SAVE_PATH}"
    headers = _api_headers(token, settings=settings)
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.ConnectError as exc:
        raise ShenbiApiError(f"无法连接神笔平台（保存应用菜单）：{exc}") from exc

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        raise ShenbiApiError(
            f"保存应用菜单失败 HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )

    if isinstance(body, dict):
        code = body.get("code")
        if code not in SUCCESS_CODES:
            raise ShenbiApiError(
                f"保存应用菜单失败（code={code}）：{_explain_api_code(body)}",
                status_code=resp.status_code,
                body=body,
            )
    return body if isinstance(body, dict) else {"raw": body}


def extract_menu_id(response: dict) -> str | None:
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if isinstance(data, str) and data.strip():
        return data.strip()
    if isinstance(data, dict):
        for key in ("menuId", "id"):
            if data.get(key):
                return str(data[key])
    return None


def ensure_flow_menu_for_app(
    *,
    app_id: str,
    title: str,
    form_id: str,
    proc_id: str,
    proc_key: str,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """确保应用预览页出现流程入口，且菜单 ID 与 model-save 的 formId/procId 对齐。"""
    existing = find_flow_menu(
        app_id,
        form_id=form_id,
        proc_id=proc_id,
        title=title,
        settings=settings,
        token=token,
    )
    extend = existing.get("extendInfo") if isinstance(existing, dict) and isinstance(existing.get("extendInfo"), dict) else {}
    needs_rebind = bool(
        existing
        and (
            str(extend.get("trendsFlowId") or "") != form_id
            or str(extend.get("queryId") or "") != proc_id
        )
    )
    payload = build_flow_menu_payload(
        app_id=app_id,
        title=title,
        form_id=form_id,
        proc_id=proc_id,
        proc_key=proc_key,
        menu_id=str(existing["menuId"]) if existing and existing.get("menuId") else None,
        sort_number=int(existing.get("sortNumber") or 5) if existing else 5,
    )
    if existing and not needs_rebind and str(existing.get("title") or "").strip() == title.strip():
        mode = "existing"
    elif existing:
        mode = "rebind"
    else:
        mode = "create"
    if mode != "existing":
        response = save_app_menu(payload, settings=settings, token=token)
    else:
        response = {"code": 200, "data": existing.get("menuId")}
    menu_id = extract_menu_id(response) or (str(existing["menuId"]) if existing and existing.get("menuId") else None)
    return {
        "step": "应用流程菜单",
        "mode": mode,
        "menu_id": menu_id,
        "title": title,
        "response": response,
    }


def ensure_app_for_workflow(
    workflow_name: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
    registry: dict | None = None,
    app_mc: str | None = None,
    form_slug: str | None = None,
    template_mode: bool = False,
    sector_title: str | None = None,
    sector_index: int = 0,
) -> tuple[str, dict]:
    """调用 tBaidaApp/saveOrUpdate：优先复用同名应用，否则新建。"""
    from core.workflow.shenbi_builder import display_workflow_name

    settings = settings or get_settings()
    registry = registry or load_workflow_registry(workflow_name, settings=settings)
    if template_mode:
        reg_id = str(registry.get("app_id") or "") if registry_app_verified(registry) else ""
        app_id = reg_id if reg_id and app_exists_on_platform(reg_id, settings=settings, token=token) else None
        resolve_mode = "registry" if app_id else "create"
    else:
        app_id, resolve_mode = resolve_app_id_for_workflow(
            workflow_name,
            registry=registry,
            settings=settings,
            token=token,
        )
    mode = {"registry": "update", "reuse": "reuse", "create": "create"}[resolve_mode]

    payload = build_app_save_payload(
        workflow_name,
        app_id=app_id,
        app_mc=app_mc or (registry.get("app_mc") if registry else None) or display_workflow_name(workflow_name),
        form_slug=form_slug,
        sector_title=sector_title,
        sector_index=sector_index,
        settings=settings,
    )

    try:
        response = save_or_update_app(payload, settings=settings, token=token)
    except ShenbiApiError as exc:
        if mode == "create" and _app_name_exists_error(exc):
            if template_mode and payload.get("mc"):
                candidates = [str(payload["mc"])]
            else:
                candidates = collect_app_name_candidates(workflow_name, registry)
            matches = find_app_ids_by_name_candidates(candidates, settings=settings, token=token)
            if not matches:
                raise
            app_id = matches[0][0]
            payload = build_app_save_payload(
                workflow_name,
                app_id=app_id,
                app_mc=payload.get("mc"),
                form_slug=form_slug,
                sector_title=sector_title,
                sector_index=sector_index,
                settings=settings,
            )
            response = save_or_update_app(payload, settings=settings, token=token)
            mode = "reuse"
        else:
            raise

    app_id = extract_app_id(response) or app_id
    if app_id and not app_exists_on_platform(app_id, settings=settings, token=token):
        payload.pop("id", None)
        response = save_or_update_app(payload, settings=settings, token=token)
        app_id = extract_app_id(response) or find_app_id_by_mc(str(payload.get("mc") or ""), settings=settings, token=token)
        mode = "create"
    if not app_id:
        raise ShenbiApiError("创建应用后未返回 appId", body=response)
    if not app_exists_on_platform(app_id, settings=settings, token=token):
        raise ShenbiApiError(
            f"应用保存后仍未在平台应用列表中找到（appId={app_id}，名称={payload.get('mc')}）。"
            "请在「我的应用」搜索完整应用名（含 AI_ 前缀）。",
            body=response,
        )
    return app_id, {
        "step": "应用",
        "mode": mode,
        "app_id": app_id,
        "app_mc": payload.get("mc"),
        "response": response,
    }


def extract_platform_ids(model: dict) -> tuple[str | None, str | None]:
    form_model = model.get("formModel") if isinstance(model.get("formModel"), dict) else {}
    proc = model.get("wfSimpleProc") if isinstance(model.get("wfSimpleProc"), dict) else {}
    form_id = form_model.get("formId")
    proc_id = proc.get("id")
    if form_id and proc_id:
        return str(form_id), str(proc_id)
    return None, None


def resolve_platform_ids(
    payloads: dict,
    workflow_name: str,
    *,
    settings: Settings | None = None,
) -> tuple[str | None, str | None, str | None]:
    """返回 (form_id, proc_id, app_id)。仅已验证 registry 视为更新，忽略模型内占位 ID。"""
    settings = settings or get_settings()
    registry = load_workflow_registry(workflow_name, settings=settings)
    if registry_app_verified(registry):
        return (
            str(registry["form_id"]),
            str(registry["proc_id"]),
            str(registry.get("app_id") or ""),
        )
    return None, None, None


def apply_registry_to_payloads(
    payloads: dict,
    workflow_name: str,
    *,
    settings: Settings | None = None,
) -> dict:
    """生成 JSON 后注入 appId；verified registry 存在时保留 form/proc/procKey 走更新。"""
    settings = settings or get_settings()
    registry = load_workflow_registry(workflow_name, settings=settings)
    if not registry_app_verified(registry) or not registry.get("app_id"):
        return payloads
    merged = copy.deepcopy(payloads)
    model = merged.get("model")
    if not isinstance(model, dict):
        return merged
    app_id = str(registry["app_id"])
    model.setdefault("wfSimpleProc", {})["appId"] = app_id
    form_id = str(registry.get("form_id") or "")
    proc_id = str(registry.get("proc_id") or "")
    proc_key = str(registry.get("proc_key") or registry.get("table_name") or "")
    if form_id and proc_id and proc_key:
        form_model = model.setdefault("formModel", {})
        form_model["formId"] = form_id
        form_model["tableName"] = proc_key
        proc = model["wfSimpleProc"]
        proc["id"] = proc_id
        proc["fromId"] = form_id
        proc["procKey"] = proc_key
        merged["is_update"] = True
    else:
        form_model = model.setdefault("formModel", {})
        form_model.pop("formId", None)
        proc = model["wfSimpleProc"]
        proc.pop("id", None)
        proc.pop("fromId", None)
        strip_task_create_ids(model.get("wfSimpleTaskInfo"))
    merged["model"] = finalize_workflow_model(model)
    return merged


def assess_model_save_response(response: dict, *, local_model: dict) -> dict:
    """保存响应评估：复杂流程新建时平台会回传完整环节树。"""
    if not local_model_has_task_tree(local_model):
        return {"ok": True, "message": "本地无环节树，跳过评估"}
    if response_has_task_tree(response):
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        task = _decode_field(data.get("wfSimpleTaskInfo"))
        route_conds = 0
        if isinstance(task, dict):
            child = task.get("child")
            if isinstance(child, dict) and child.get("type") == "ROUTE":
                route_conds = len(child.get("conditions") or [])
        msg = "平台已回传环节树"
        if route_conds:
            msg += f"（含 {route_conds} 路并行分支）"
        return {"ok": True, "message": msg}
    return {
        "ok": False,
        "message": (
            "model-save 未在响应中回传环节树（复杂流程写入仍可能成功，请在设计器中确认）。"
        ),
    }


def _count_route_branches(task_root: dict | None) -> int:
    if not isinstance(task_root, dict):
        return 0
    child = task_root.get("child")
    if isinstance(child, dict) and child.get("type") == "ROUTE":
        return len(child.get("conditions") or [])
    return 0


def _is_stale_registry_save_error(exc: ShenbiApiError) -> bool:
    """registry 中 formId/procId 在平台已不存在（常见于手工删表或 update 半失败）。"""
    body = exc.body if isinstance(getattr(exc, "body", None), dict) else {}
    msg = str(body.get("msg") or body.get("message") or exc.args[0] or "")
    markers = ("formId", "FORM_MODEL", "procId", "流程不存在", "不存在该表单", "不存在该流程")
    return any(m in msg for m in markers)


def _is_model_conversion_error(exc: ShenbiApiError) -> bool:
    body = exc.body if isinstance(getattr(exc, "body", None), dict) else {}
    code = body.get("code")
    msg = str(body.get("msg") or body.get("message") or "")
    return code in (13, "13") or "模型转换失败" in msg


def _is_recoverable_model_save_error(exc: ShenbiApiError) -> bool:
    return _is_model_conversion_error(exc) or _is_stale_registry_save_error(exc)


def normalize_model_tenant_ids(model: dict, tenant_id: str) -> None:
    """保存前统一 tenantId，避免 dev/ym 环境切换后 payload 仍带旧租户导致 code=13。"""
    tid = (tenant_id or "").strip()
    if not tid:
        return
    proc = model.get("wfSimpleProc")
    if isinstance(proc, dict):
        proc["tenantId"] = tid
    task_root = model.get("wfSimpleTaskInfo")
    if not isinstance(task_root, dict):
        return
    for task in iter_all_tasks(task_root):
        if isinstance(task.get("tenantId"), str) or task.get("tenantId") is not None:
            task["tenantId"] = tid
        props = task.get("properties")
        if not isinstance(props, dict):
            continue
        for collection_key in ("personList", "buttonList", "fromPropertyList", "branchConditionList"):
            for item in props.get(collection_key) or []:
                if isinstance(item, dict):
                    item["tenantId"] = tid


def _sync_settings_tenant(settings: Settings) -> Settings:
    from core.config.shenbi_environments import resolve_shenbi_tenant_id

    tenant_id = resolve_shenbi_tenant_id(settings)
    if settings.shenbi_tenant_id == tenant_id:
        return settings
    return settings.model_copy(update={"shenbi_tenant_id": tenant_id})


def _create_response_degraded(
    response: dict,
    local_tree: dict | None,
    *,
    min_route_branches: int,
) -> bool:
    """平台 create 响应相对本地树是否为空壳/不完整。"""
    if not response_has_task_tree(response):
        return True
    create_tree = task_tree_from_save_response(response)
    if isinstance(create_tree, dict) and task_tree_degraded(create_tree, local_tree):
        return True
    return _response_looks_like_shell(response, min_route_branches=min_route_branches)


def _apply_create_save_with_shell_recovery(
    model: dict,
    body: dict,
    *,
    local_tree: dict | None,
    min_route_branches: int,
    settings: Settings | None,
    token: str | None,
    results: list[dict],
    recreate: bool,
) -> tuple[dict, dict]:
    """model-save create；空壳时自动重试一次并保留本地环节树。"""
    settings = _sync_settings_tenant(settings or get_settings())
    normalize_model_tenant_ids(body, settings.shenbi_tenant_id)
    try:
        response = save_proc_model(body, settings=settings, token=token)
    except ShenbiApiError as exc:
        if _is_model_conversion_error(exc):
            normalize_model_tenant_ids(body, settings.shenbi_tenant_id)
            response = save_proc_model(body, settings=settings, token=token)
        else:
            raise
    save_hint = assess_model_save_response(response, local_model=body)
    mode = "create" if not recreate else "create（重建）"
    results.append(
        {
            "step": "表单与流程",
            "mode": mode,
            "response": response,
            "detail": save_hint.get("message"),
        }
    )

    degraded = _create_response_degraded(
        response, local_tree, min_route_branches=min_route_branches
    )
    if degraded and local_task_tree_intact(local_tree):
        retry_response = save_proc_model(body, settings=settings, token=token)
        retry_hint = assess_model_save_response(retry_response, local_model=body)
        results.append(
            {
                "step": "空壳恢复",
                "mode": "retry",
                "response": retry_response,
                "detail": retry_hint.get("message") or "检测到空壳响应，已自动重试 create",
            }
        )
        if not _create_response_degraded(
            retry_response, local_tree, min_route_branches=min_route_branches
        ):
            response = retry_response
            degraded = False

    model = finalize_workflow_model(merge_save_response_preserve_tree(model, response))
    create_tree = task_tree_from_save_response(response)
    if isinstance(create_tree, dict):
        merge_platform_task_tree_inplace(model.get("wfSimpleTaskInfo"), create_tree)
        merge_platform_task_ids(model.get("wfSimpleTaskInfo"), create_tree)

    if degraded and local_task_tree_intact(local_tree):
        model["wfSimpleTaskInfo"] = copy.deepcopy(local_tree)
        if isinstance(create_tree, dict):
            merge_platform_task_ids(model.get("wfSimpleTaskInfo"), create_tree)
            sync_from_property_list_task_ids(model.get("wfSimpleTaskInfo"))
        results.append(
            {
                "step": "空壳恢复",
                "mode": "warn",
                "detail": (
                    "平台 create 响应环节树不完整，已保留本地环节树并继续绑定菜单/权限；"
                    "请在设计器确认流程结构"
                ),
            }
        )

    return model, response


def _response_looks_like_shell(response: dict, *, min_route_branches: int = 2) -> bool:
    """平台回传环节树为空壳（办理 start / 仅 STARTTASK / 无并行 ROUTE）。"""
    if not response_has_task_tree(response):
        return True
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    task = _decode_field(data.get("wfSimpleTaskInfo"))
    if _complex_task_tree_collapsed(
        task if isinstance(task, dict) else None,
        min_routes=min_route_branches,
    ):
        return True
    if not isinstance(task, dict):
        return False
    name = str(task.get("taskName") or "").replace(" ", "")
    if name in {"办理start", "办理"} or "办理start" in name:
        return True
    branches = _count_route_branches(task)
    return 0 < branches < min_route_branches


def _prepare_model_save_body(
    model: dict,
    *,
    app_id: str,
    is_update: bool,
    form_id: str | None,
    proc_id: str | None,
) -> dict:
    """组装 model-save 请求体。create 时 strip 环节 id 且 formModelJson=null。"""
    if is_update:
        body = apply_saved_ids(
            sync_form_model_json(model),
            form_id=str(form_id or ""),
            proc_id=str(proc_id or ""),
        )
        body.setdefault("wfSimpleProc", {})["appId"] = app_id
        return finalize_workflow_model(body)

    body = strip_create_ids(sync_form_model_json(model))
    strip_task_create_ids(body.get("wfSimpleTaskInfo"))
    body = finalize_workflow_model(body)
    body["formModelJson"] = None
    body.setdefault("wfSimpleProc", {})["appId"] = app_id
    return body


def _prepare_permission_patch_body(
    local_model: dict,
    *,
    platform_tree: dict | None,
    app_id: str,
    form_id: str,
    proc_id: str,
) -> dict:
    """权限补写：保留完整本地拓扑 + 平台 id 链，仅覆盖 fromPropertyList；formModelJson=null。"""
    from core.workflow.shenbi_builder import (
        _attach_form_permissions,
        _normalize_form_permissions,
    )

    local_ready = finalize_workflow_model(copy.deepcopy(local_model))
    local_tree = local_ready.get("wfSimpleTaskInfo")
    local_permissions = (
        copy.deepcopy(local_tree) if isinstance(local_tree, dict) else None
    )

    if (
        isinstance(platform_tree, dict)
        and platform_tree.get("type")
        and not _complex_task_tree_collapsed(platform_tree)
    ):
        body = apply_proc_form_ids_only(local_model, form_id=form_id, proc_id=proc_id)
        body["wfSimpleTaskInfo"] = copy.deepcopy(platform_tree)
        if isinstance(local_permissions, dict):
            merge_local_form_permissions_by_task_key(body["wfSimpleTaskInfo"], local_permissions)
    else:
        body = apply_proc_form_ids_only(local_ready, form_id=form_id, proc_id=proc_id)
        if isinstance(platform_tree, dict) and platform_tree.get("type"):
            merge_platform_task_tree_inplace(body.get("wfSimpleTaskInfo"), platform_tree)
        _attach_form_permissions(body)
        body = _normalize_form_permissions(body)

    body.setdefault("wfSimpleProc", {})["appId"] = app_id
    prepare_from_property_list_for_platform_save(body.get("wfSimpleTaskInfo"))
    body["formModelJson"] = None
    return body


def _prepare_full_tree_update_body(
    model: dict,
    *,
    local_tree: dict,
    app_id: str,
    form_id: str,
    proc_id: str,
) -> dict:
    """create 空壳后：用完整本地环节树 update 覆盖平台（strip 环节 id 后全量写入）。"""
    from core.workflow.shenbi_builder import _attach_form_permissions, _normalize_form_permissions

    body = apply_saved_ids(sync_form_model_json(copy.deepcopy(model)), form_id=form_id, proc_id=proc_id)
    body["wfSimpleTaskInfo"] = copy.deepcopy(local_tree)
    strip_task_create_ids(body["wfSimpleTaskInfo"])
    _attach_form_permissions(body)
    body = _normalize_form_permissions(body)
    body.setdefault("wfSimpleProc", {})["appId"] = app_id
    prepare_from_property_list_for_platform_save(body.get("wfSimpleTaskInfo"))
    body["formModelJson"] = None
    return finalize_workflow_model(body)


def _push_full_task_tree_update(
    model: dict,
    *,
    local_tree: dict,
    app_id: str,
    form_id: str,
    proc_id: str,
    settings: Settings | None,
    token: str | None,
    results: list[dict],
) -> tuple[dict, dict]:
    """平台环节树空壳时，追加 update save 写入完整 DAG。"""
    body = _prepare_full_tree_update_body(
        model,
        local_tree=local_tree,
        app_id=app_id,
        form_id=form_id,
        proc_id=proc_id,
    )
    response = save_proc_model(body, settings=settings, token=token)
    resp_task = task_tree_from_save_response(response)
    if isinstance(resp_task, dict) and task_tree_degraded(resp_task, local_tree):
        response = save_proc_model(body, settings=settings, token=token)
        resp_task = task_tree_from_save_response(response)

    merged = merge_save_response_preserve_tree(model, response)
    if isinstance(resp_task, dict) and not task_tree_degraded(resp_task, local_tree):
        merged["wfSimpleTaskInfo"] = copy.deepcopy(resp_task)
        results.append(
            {
                "step": "环节树补写",
                "mode": "update",
                "detail": "已用完整环节树 update 覆盖平台空壳",
            }
        )
    elif local_task_tree_intact(local_tree):
        merged["wfSimpleTaskInfo"] = copy.deepcopy(local_tree)
        if isinstance(resp_task, dict):
            merge_platform_task_ids(merged["wfSimpleTaskInfo"], resp_task)
            sync_from_property_list_task_ids(merged["wfSimpleTaskInfo"])
        results.append(
            {
                "step": "环节树补写",
                "mode": "warn",
                "detail": "环节树 update 后平台仍不完整，请在设计器确认",
            }
        )
    else:
        results.append(
            {
                "step": "环节树补写",
                "mode": "warn",
                "detail": "本地环节树不可用，跳过空壳覆盖",
            }
        )
    return merged, response


def _patch_form_permissions_after_create(
    model: dict,
    *,
    create_response: dict,
    app_id: str,
    form_id: str,
    proc_id: str,
    proc_key: str,
    local_tree: dict | None,
    settings: Settings | None,
    token: str | None,
) -> tuple[dict, dict | None, dict]:
    """create 后补写 fromPropertyList；须基于完整环节树，否则跳过以免冲垮 ROUTE。"""
    create_tree = task_tree_from_save_response(create_response)
    fetched: dict | None = None

    platform_tree: dict | None = None
    if isinstance(create_tree, dict) and not task_tree_degraded(create_tree, local_tree):
        platform_tree = create_tree
    else:
        if isinstance(create_tree, dict):
            merge_platform_task_tree_inplace(model.get("wfSimpleTaskInfo"), create_tree)
        fetched = fetch_platform_model(
            form_id=form_id,
            proc_id=proc_id,
            proc_key=proc_key,
            app_id=app_id,
            settings=settings,
            token=token,
        )
        if isinstance(fetched, dict):
            fetched_tree = fetched.get("wfSimpleTaskInfo")
            if isinstance(fetched_tree, dict) and not task_tree_degraded(fetched_tree, local_tree):
                platform_tree = fetched_tree

    tree_for_id = platform_tree if platform_tree is not None else model.get("wfSimpleTaskInfo")
    if not _start_task_has_platform_id(tree_for_id):
        return (
            model,
            None,
            {
                "ok": False,
                "skipped": True,
                "message": "跳过权限补写：未拿到 STARTTASK 平台 id，环节树保持 create 结果",
            },
        )

    perm_body = _prepare_permission_patch_body(
        model,
        platform_tree=platform_tree,
        app_id=app_id,
        form_id=form_id,
        proc_id=proc_id,
    )
    if task_tree_degraded(perm_body.get("wfSimpleTaskInfo"), local_tree):
        return (
            model,
            None,
            {
                "ok": False,
                "skipped": True,
                "message": "跳过权限补写：补写载荷环节树不完整，避免覆盖平台 ROUTE 树",
            },
        )

    response = save_proc_model(perm_body, settings=settings, token=token)
    resp_task = task_tree_from_save_response(response)
    if isinstance(resp_task, dict) and task_tree_degraded(resp_task, local_tree):
        raise ShenbiApiError(
            "权限补写后平台环节树塌陷；请重新一键写入（create 环节树应仍保留，可在设计器确认）"
        )

    merged = merge_save_response_preserve_tree(model, response)
    hint = assess_model_save_response(response, local_model=perm_body)
    hint["ok"] = True
    return merged, response, hint


def save_workflow_models(
    payloads: dict,
    *,
    workflow_name: str,
    settings: Settings | None = None,
    token: str | None = None,
    recreate: bool = False,
    template_mode: bool = False,
    app_mc: str | None = None,
    form_slug: str | None = None,
    sector_title: str | None = None,
    sector_index: int = 0,
) -> dict:
    """先 ensure 应用，再 model-save 全量 create 表单与流程（统一写入路径）。"""
    settings = _sync_settings_tenant(settings or get_settings())
    registry = load_workflow_registry(workflow_name, settings=settings)
    if template_mode and not registry_app_verified(registry):
        registry = None
    legacy = False if template_mode else is_legacy_registry(registry, settings=settings)

    model = payloads.get("model")
    if not isinstance(model, dict):
        raise ShenbiApiError("payload 缺少 model 字段")

    results: list[dict] = []
    if recreate and registry:
        results.append(
            {
                "step": "重建",
                "mode": "create",
                "detail": (
                    f"忽略旧 procId={str(registry.get('proc_id', ''))[:8]}…，"
                    "将新建表单/流程并重新绑定菜单"
                ),
            }
        )
    if legacy:
        results.append(
            {
                "step": "迁移",
                "mode": "legacy_registry",
                "detail": "检测到旧版 registry（appId 来自 .env 默认），将创建独立应用并新建表单/流程",
            }
        )

    app_id, app_step = ensure_app_for_workflow(
        workflow_name,
        settings=settings,
        token=token,
        registry=registry if registry_app_verified(registry) else None,
        app_mc=app_mc or payloads.get("app_display_name"),
        form_slug=form_slug,
        template_mode=template_mode,
        sector_title=sector_title,
        sector_index=sector_index,
    )
    results.append(app_step)

    model = finalize_workflow_model(copy.deepcopy(model))
    normalize_model_tenant_ids(model, settings.shenbi_tenant_id)
    local_tree_snapshot = copy.deepcopy(model.get("wfSimpleTaskInfo"))
    min_route_branches = min_route_branches_for_local(local_tree_snapshot)
    model.setdefault("wfSimpleProc", {})["appId"] = app_id

    use_update = bool(
        not recreate
        and registry_app_verified(registry)
        and registry.get("form_id")
        and registry.get("proc_id")
        and registry.get("proc_key")
        and not legacy
    )
    full_tree_save_used = False
    update_form_id = str(registry.get("form_id") or "") if use_update and registry else ""
    update_proc_id = str(registry.get("proc_id") or "") if use_update and registry else ""
    update_proc_key = str(registry.get("proc_key") or registry.get("table_name") or "") if use_update and registry else ""

    if use_update:
        if isinstance(model.get("formModel"), dict):
            model["formModel"]["tableName"] = update_proc_key
            model["formModel"]["formId"] = update_form_id
        model["wfSimpleProc"]["procKey"] = update_proc_key
        model["wfSimpleProc"]["id"] = update_proc_id
        model["wfSimpleProc"]["fromId"] = update_form_id
        results.append(
            {
                "step": "写入策略",
                "mode": "update",
                "detail": f"更新已有表单/流程（procKey={update_proc_key}）",
            }
        )
        if local_task_tree_intact(local_tree_snapshot):
            body = _prepare_full_tree_update_body(
                model,
                local_tree=local_tree_snapshot,
                app_id=app_id,
                form_id=update_form_id,
                proc_id=update_proc_id,
            )
            full_tree_save_used = True
        else:
            body = _prepare_model_save_body(
                model,
                app_id=app_id,
                is_update=True,
                form_id=update_form_id,
                proc_id=update_proc_id,
            )
        try:
            response = save_proc_model(body, settings=settings, token=token)
        except ShenbiApiError as exc:
            if _is_recoverable_model_save_error(exc):
                use_update = False
                form_model = model.setdefault("formModel", {})
                form_model.pop("formId", None)
                proc = model.setdefault("wfSimpleProc", {})
                proc.pop("id", None)
                proc.pop("fromId", None)
                proc.pop("procKey", None)
                strip_task_create_ids(model.get("wfSimpleTaskInfo"))
                results.append(
                    {
                        "step": "写入策略",
                        "mode": "warn",
                        "detail": "registry 中 form/proc 已失效或租户不匹配（code=13），将改为全量新建",
                    }
                )
            else:
                raise
        else:
            save_hint = assess_model_save_response(response, local_model=body)
            results.append(
                {
                    "step": "表单与流程",
                    "mode": "update",
                    "response": response,
                    "detail": save_hint.get("message"),
                }
            )
            model = merge_save_response_preserve_tree(model, response)

    if not use_update:
        results.append(
            {
                "step": "写入策略",
                "mode": "create",
                "detail": "全量新建表单/流程并绑定菜单（由 DAG 拓扑生成环节树，不区分线性/复杂）",
            }
        )
        body = _prepare_model_save_body(
            model,
            app_id=app_id,
            is_update=False,
            form_id=None,
            proc_id=None,
        )
        model, response = _apply_create_save_with_shell_recovery(
            model,
            body,
            local_tree=local_tree_snapshot,
            min_route_branches=min_route_branches,
            settings=settings,
            token=token,
            results=results,
            recreate=recreate,
        )

    form_id, proc_id = extract_platform_ids(model)
    if not form_id or not proc_id:
        raise ShenbiApiError("保存后未返回 formId / procId", body=response)

    proc = model.get("wfSimpleProc") if isinstance(model.get("wfSimpleProc"), dict) else {}
    proc_key = str(proc.get("procKey") or "")

    platform_task = model.get("wfSimpleTaskInfo")
    resp_task_after_save = task_tree_from_save_response(response)
    needs_tree_push = (
        local_task_tree_intact(local_tree_snapshot)
        and (
            not response_has_task_tree(response)
            or (
                isinstance(resp_task_after_save, dict)
                and task_tree_degraded(resp_task_after_save, local_tree_snapshot)
            )
            or (
                isinstance(platform_task, dict)
                and task_tree_degraded(platform_task, local_tree_snapshot)
            )
        )
    )
    if needs_tree_push:
        model, response = _push_full_task_tree_update(
            model,
            local_tree=local_tree_snapshot,
            app_id=app_id,
            form_id=str(form_id),
            proc_id=str(proc_id),
            settings=settings,
            token=token,
            results=results,
        )
        full_tree_save_used = True
        form_id, proc_id = extract_platform_ids(model)
        proc = model.get("wfSimpleProc") if isinstance(model.get("wfSimpleProc"), dict) else {}
        proc_key = str(proc.get("procKey") or proc_key)

    tree_after_push = model.get("wfSimpleTaskInfo")
    resp_task_confirmed = task_tree_from_save_response(response)
    tree_push_confirmed = (
        response_has_task_tree(response)
        and isinstance(resp_task_confirmed, dict)
        and not task_tree_degraded(resp_task_confirmed, local_tree_snapshot)
    )
    skip_permission_patch = bool(
        full_tree_save_used
        or tree_push_confirmed
        or (use_update and local_task_tree_intact(local_tree_snapshot))
    )
    permissions_already_on_tree = skip_permission_patch and local_task_tree_intact(local_tree_snapshot)

    try:
        if permissions_already_on_tree:
            perm_response = None
            if full_tree_save_used or (use_update and local_task_tree_intact(local_tree_snapshot)):
                perm_message = "strip-id 全量环节树已写入，跳过权限补写（避免覆盖设计器环节树）"
            else:
                perm_message = "完整环节树已写入平台，跳过二次权限补写"
            perm_hint = {
                "ok": True,
                "skipped": True,
                "message": perm_message,
            }
            model = finalize_workflow_model(model)
        else:
            model, perm_response, perm_hint = _patch_form_permissions_after_create(
                model,
                create_response=response,
                app_id=app_id,
                form_id=str(form_id),
                proc_id=str(proc_id),
                proc_key=proc_key,
                local_tree=local_tree_snapshot,
                settings=settings,
                token=token,
            )
        perm_mode = "warn" if perm_hint.get("skipped") else "update"
        results.append(
            {
                "step": "表单操作权限",
                "mode": perm_mode,
                "response": perm_response,
                "detail": perm_hint.get("message"),
            }
        )
        if (
            not perm_hint.get("skipped")
            and task_tree_degraded(model.get("wfSimpleTaskInfo"), local_tree_snapshot)
            and local_task_tree_intact(local_tree_snapshot)
        ):
            platform_ids_tree = copy.deepcopy(model.get("wfSimpleTaskInfo"))
            model["wfSimpleTaskInfo"] = copy.deepcopy(local_tree_snapshot)
            if isinstance(platform_ids_tree, dict):
                merge_platform_task_ids(model["wfSimpleTaskInfo"], platform_ids_tree)
            sync_from_property_list_task_ids(model.get("wfSimpleTaskInfo"))
            results.append(
                {
                    "step": "空壳恢复",
                    "mode": "warn",
                    "detail": "权限补写后环节树塌陷，已回退为本地环节树（权限可能未完全绑定）",
                }
            )
    except ShenbiApiError as exc:
        if local_task_tree_intact(local_tree_snapshot):
            model["wfSimpleTaskInfo"] = copy.deepcopy(local_tree_snapshot)
            results.append(
                {
                    "step": "表单操作权限",
                    "mode": "warn",
                    "detail": f"{exc.args[0]}；已保留本地环节树",
                }
            )
        else:
            results.append(
                {
                    "step": "表单操作权限",
                    "mode": "warn",
                    "detail": str(exc.args[0]),
                }
            )
            raise

    proc = model.get("wfSimpleProc") if isinstance(model.get("wfSimpleProc"), dict) else {}
    proc_key = str(proc.get("procKey") or "")
    app_id = str(proc.get("appId") or app_id)
    table_name = (model.get("formModel") or {}).get("tableName")
    if proc_key and str(table_name or "") != proc_key:
        table_name = proc_key
        if isinstance(model.get("formModel"), dict):
            model["formModel"]["tableName"] = proc_key
    app_mc = app_step.get("app_mc")

    save_workflow_registry(
        workflow_name,
        app_id=app_id,
        app_mc=str(app_mc) if app_mc else None,
        form_id=form_id,
        proc_id=proc_id,
        proc_key=proc_key,
        table_name=str(table_name) if table_name else proc_key,
        task_id_by_key=extract_task_id_by_key(model.get("wfSimpleTaskInfo")),
        settings=settings,
    )

    from core.workflow.shenbi_builder import display_workflow_name

    menu_title = str(app_mc or display_workflow_name(workflow_name))
    menu_steps = rebind_flow_menus_for_workflow_aliases(
        workflow_name=workflow_name,
        primary_app_id=app_id,
        title=menu_title,
        form_id=form_id,
        proc_id=proc_id,
        proc_key=proc_key,
        registry=registry,
        settings=settings,
        token=token,
    )
    results.extend(menu_steps)

    publish_result = publish_workflow(
        proc_id=proc_id,
        proc_key=proc_key,
        form_id=form_id,
        app_id=app_id,
        settings=settings,
        token=token,
    )
    runtime_check = verify_runtime_proc(
        proc_key=proc_key,
        proc_id=proc_id,
        app_id=app_id,
        settings=settings,
        token=token,
    )
    publish_mode = "ok" if publish_result.get("ok") else "warn"
    runtime_mode = "ok" if runtime_check.get("ok") else "warn"
    if not session_cookie_configured(settings) and not publish_result.get("ok"):
        publish_detail = (
            f"{publish_result.get('message') or '发布未成功'}；"
            "请在侧边栏填写浏览器 DevTools 中的完整 SESSION Cookie 后重新写入或批量发布。"
        )
    else:
        publish_detail = publish_result.get("message")
    results.append(
        {
            "step": "流程发布",
            "mode": publish_mode,
            "detail": publish_detail,
        }
    )
    results.append(
        {
            "step": "运行时校验",
            "mode": runtime_mode,
            "detail": runtime_check.get("message"),
        }
    )
    if not runtime_check.get("ok"):
        if session_cookie_configured(settings):
            raise ShenbiApiError(
                str(runtime_check.get("message") or publish_result.get("message") or "流程未发布到运行时"),
                body={"publish": publish_result, "runtime_check": runtime_check},
            )
        results.append(
            {
                "step": "写入结论",
                "mode": "warn",
                "detail": (
                    "模型已保存，但未能验证运行时发布（未配置有效 SESSION Cookie）。"
                    "请填写 Cookie 后点击「批量发布流程」。"
                ),
            }
        )
    model_check = verify_model_integrity(model)
    persisted_check = verify_persisted_model(
        form_id=form_id,
        proc_id=proc_id,
        proc_key=proc_key,
        app_id=app_id,
        local_model=model,
        settings=settings,
        token=token,
    )
    results.append(
        {
            "step": "模型校验",
            "mode": "local" if model_check.get("ok") else "warn",
            "detail": model_check.get("message"),
            "checks": model_check.get("checks"),
        }
    )
    results.append(
        {
            "step": "平台回读",
            "mode": "ok" if persisted_check.get("ok") else "warn",
            "detail": persisted_check.get("message"),
            **{k: persisted_check[k] for k in ("endpoint", "remote_task_types") if k in persisted_check},
        }
    )

    if task_tree_has_routes(local_tree_snapshot):
        write_outcome = assess_complex_write_outcome(
            save_response=response,
            local_model=model,
            persisted_check=persisted_check,
        )
        if write_outcome["level"] == "warn" or (
            write_outcome["level"] == "error" and local_task_tree_intact(local_tree_snapshot)
        ):
            if write_outcome["level"] == "error":
                write_outcome = {
                    **write_outcome,
                    "ok": True,
                    "level": "warn",
                    "message": (
                        f"{write_outcome['message']}；本地环节树完整，已保留并在设计器绑定菜单"
                    ),
                }
        if write_outcome["level"] == "warn":
            persisted_check = {
                **persisted_check,
                "ok": True,
                "message": write_outcome["message"],
            }
            results.append(
                {
                    "step": "写入结论",
                    "mode": "warn",
                    "detail": write_outcome["message"],
                }
            )
        elif not write_outcome["ok"]:
            raise ShenbiApiError(write_outcome["message"])

    return {
        "model": model,
        "results": results,
        "is_update": use_update,
        "form_id": form_id,
        "proc_id": proc_id,
        "proc_key": proc_key,
        "app_id": app_id,
        "app_mc": app_mc,
        "publish": publish_result,
        "runtime_check": runtime_check,
        "runtime_ready": bool(runtime_check.get("ok")),
        "model_check": model_check,
        "persisted_check": persisted_check,
    }


def _response_data_ok(data: Any) -> bool:
    """神笔部分接口 code=200 但 data=0 表示未命中（发布/查询失败）。"""
    if data is None:
        return False
    if data in (0, "0", False):
        return False
    if isinstance(data, dict) and not data:
        return False
    return True


def session_cookie_configured(settings: Settings | None = None) -> bool:
    """是否配置了可用于发布/运行时查询的有效浏览器 Cookie。"""
    settings = settings or get_settings()
    cookie = (settings.shenbi_session_cookie or "").strip()
    if not cookie:
        return False
    normalized = cookie.replace(" ", "")
    if normalized in {"SESSION=SESSION", "SESSION"}:
        return False
    if "SESSION=" in cookie and len(normalized) > len("SESSION=") + 8:
        return True
    return len(normalized) > 24


def probe_publish_capability(
    *,
    settings: Settings | None = None,
    token: str | None = None,
    workflow_name: str | None = None,
) -> dict:
    """探测当前 token + Cookie 能否发布/查询运行时流程。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    result = {
        "cookie_configured": session_cookie_configured(settings),
        "publish_ok": False,
        "runtime_ok": False,
        "message": "",
    }
    if not token:
        result["message"] = "未配置 token"
        return result
    if not result["cookie_configured"]:
        result["message"] = "SESSION Cookie 仍为占位值；将尝试仅用 token 发布（可能失败，建议填写 Cookie）"
    registry = None
    if workflow_name:
        registry = load_workflow_registry(workflow_name, settings=settings)
    if not registry:
        for key in ("集团费用报销单", "外贸集团样车合同备案表", "野马集团二线招聘需求表"):
            registry = load_workflow_registry(key, settings=settings)
            if registry:
                break
    if not registry:
        result["message"] = "无本地 registry，请先完成至少一次流程写入"
        return result
    publish = publish_workflow(
        proc_id=str(registry["proc_id"]),
        proc_key=str(registry.get("proc_key") or registry.get("table_name") or ""),
        form_id=str(registry.get("form_id") or ""),
        app_id=str(registry.get("app_id") or ""),
        settings=settings,
        token=token,
    )
    runtime = verify_runtime_proc(
        proc_key=str(registry.get("proc_key") or registry.get("table_name") or ""),
        proc_id=str(registry.get("proc_id") or ""),
        app_id=str(registry.get("app_id") or ""),
        settings=settings,
        token=token,
    )
    result["publish_ok"] = bool(publish.get("ok"))
    result["runtime_ok"] = bool(runtime.get("ok"))
    if result["runtime_ok"]:
        result["message"] = "发布与运行时查询正常，可批量写入"
    elif result["publish_ok"]:
        result["message"] = "发布接口成功，但运行时仍未命中（请确认 Cookie 与 token 同一会话）"
    else:
        result["message"] = publish.get("message") or runtime.get("message") or "发布探测失败"
    return result


def verify_model_integrity(model: dict) -> dict:
    """写入后本地 model 完整性校验（环节树、办理人、procName）。"""
    task_root = model.get("wfSimpleTaskInfo")
    proc = model.get("wfSimpleProc") if isinstance(model.get("wfSimpleProc"), dict) else {}
    form_model = model.get("formModel") if isinstance(model.get("formModel"), dict) else {}

    task_types: dict[str, int] = {}
    if isinstance(task_root, dict):
        task_types = count_task_types(task_root)

    missing_person: list[str] = []
    if isinstance(task_root, dict):
        for task in iter_all_tasks(task_root):
            if task.get("type") not in {"USERTASK", "CCTASK"}:
                continue
            props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
            if not _has_assignable_person_list(props.get("personList")):
                missing_person.append(str(task.get("taskName") or task.get("taskKey") or task.get("id")))

    checks = [
        {
            "name": "task_tree",
            "ok": bool(isinstance(task_root, dict) and task_root.get("type")),
            "detail": task_types or None,
        },
        {
            "name": "proc_name",
            "ok": bool(str(proc.get("procName") or "").strip()),
            "detail": proc.get("procName"),
        },
        {
            "name": "form_id",
            "ok": bool(str(form_model.get("formId") or "").strip()),
            "detail": form_model.get("formId"),
        },
        {
            "name": "person_list",
            "ok": not missing_person,
            "detail": missing_person or None,
        },
    ]
    ok = all(c["ok"] for c in checks)
    parts = []
    if task_types:
        parts.append(
            "环节 "
            + "/".join(f"{k}×{v}" for k, v in sorted(task_types.items()) if v)
        )
    if missing_person:
        parts.append(f"缺办理人 {len(missing_person)} 个")
    message = "；".join(parts) if parts else ("模型完整" if ok else "模型校验未通过")
    return {"ok": ok, "checks": checks, "message": message, "task_types": task_types}


def _has_assignable_person_list(person_list: Any) -> bool:
    if not isinstance(person_list, list) or not person_list:
        return False
    first = person_list[0]
    if not isinstance(first, dict):
        return False
    person_type = str(first.get("personType") or "").strip()
    return person_type in {"role", "assignMember", "user", "dept", "post"}


def verify_persisted_model(
    *,
    form_id: str,
    proc_id: str,
    proc_key: str,
    app_id: str,
    local_model: dict,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """尝试 model-get 回读平台已存流程，与本地环节拓扑对比。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    local_types = verify_model_integrity(local_model).get("task_types") or {}

    if not token:
        return {
            "ok": False,
            "message": "未配置 token，跳过平台回读",
            "local_task_types": local_types,
        }

    base = settings.shenbi_base_url.rstrip("/")
    headers = _api_headers(token, settings=settings)
    body: dict[str, Any] = {
        "formId": form_id,
        "procId": proc_id,
        "id": proc_id,
        "procKey": proc_key,
        "appId": app_id,
    }
    path = "/fighter-baida/api/flow-simple/proc/model-get"
    last: dict[str, Any] = {"endpoint": path}
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(f"{base}{path}", json=body, headers=headers)
            try:
                payload = resp.json()
            except Exception:
                payload = {"raw": resp.text}
            data = payload.get("data") if isinstance(payload, dict) else None
            last.update(
                {
                    "code": payload.get("code") if isinstance(payload, dict) else None,
                    "msg": payload.get("msg") if isinstance(payload, dict) else None,
                }
            )
            if not (isinstance(payload, dict) and payload.get("code") in SUCCESS_CODES):
                return {
                    "ok": bool(local_types),
                    "message": "平台 model-get 未返回成功，以本地模型为准",
                    "local_task_types": local_types,
                    **last,
                }
            if not _response_data_ok(data):
                if session_cookie_configured(settings):
                    return {
                        "ok": False,
                        "message": "平台 model-get 未返回流程数据（formId/procId 可能无效或未发布）",
                        "local_task_types": local_types,
                        **last,
                    }
                return {
                    "ok": bool(local_types),
                    "message": "平台 model-get data 为空（未配置 SESSION Cookie，无法确认平台侧是否已持久化）",
                    "local_task_types": local_types,
                    **last,
                }
            if isinstance(data, dict):
                remote_task = _decode_field(data.get("wfSimpleTaskInfo"))
            else:
                remote_task = None
            if not isinstance(remote_task, dict) or not remote_task.get("type"):
                return {
                    "ok": bool(local_types),
                    "message": "平台未回传环节树（wfSimpleTaskInfo 为空），以本地模型为准",
                    "local_task_types": local_types,
                    **last,
                }
            remote_types = count_task_types(remote_task)
            type_ok = remote_types == local_types
            proc = _decode_field(data.get("wfSimpleProc")) if isinstance(data, dict) else None
            proc_name = proc.get("procName") if isinstance(proc, dict) else None
            return {
                "ok": type_ok and bool(proc_name),
                "message": (
                    f"平台环节 {remote_types} 与本地一致"
                    if type_ok
                    else f"平台环节 {remote_types} ≠ 本地 {local_types}"
                ),
                "local_task_types": local_types,
                "remote_task_types": remote_types,
                "proc_name": proc_name,
                **last,
            }
    except httpx.ConnectError as exc:
        return {
            "ok": bool(local_types),
            "message": f"平台回读连接失败：{exc}",
            "local_task_types": local_types,
            **last,
        }


def verify_runtime_proc(
    *,
    proc_key: str,
    proc_id: str | None = None,
    app_id: str | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """查询运行时流程定义是否可加载（工作台菜单依赖此数据）。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    if not token:
        return {"ok": False, "message": "未配置 token"}

    base = settings.shenbi_base_url.rstrip("/")
    headers = _api_headers(token, settings=settings)
    tenant_id = settings.shenbi_tenant_id
    body: dict[str, Any] = {
        "procKey": proc_key,
        "appId": app_id or settings.shenbi_app_id,
        "tenantId": tenant_id,
    }
    if proc_id:
        body["procId"] = proc_id
        body["id"] = proc_id

    endpoints = (
        "/fighter-baida/api/flow-simple/proc/getProcDef",
        "/fighter-baida/api/flow-simple/proc/getByKey",
        "/fighter-baida/api/flow-simple/proc/detail",
        "/fighter-baida/api/flow-simple/proc/getProcDefByKey",
    )
    last: dict | None = None
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            for path in endpoints:
                resp = client.post(f"{base}{path}", json=body, headers=headers)
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"raw": resp.text}
                data = payload.get("data") if isinstance(payload, dict) else None
                last = {
                    "endpoint": path,
                    "code": payload.get("code") if isinstance(payload, dict) else None,
                    "data": data,
                    "msg": payload.get("msg") if isinstance(payload, dict) else None,
                }
                if isinstance(payload, dict) and payload.get("code") in SUCCESS_CODES:
                    if _response_data_ok(data):
                        return {"ok": True, "message": "运行时流程定义可查询", **last}
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"查询连接失败：{exc}"}

    cookie = (settings.shenbi_session_cookie or "").strip()
    hint = (
        "运行时未找到流程定义，工作台会提示「查无该流程定义信息」。"
        "请在神笔设计器对该流程点击「发布」，或在流程写入页填写真实 SESSION Cookie 后重新一键写入。"
    )
    if cookie in {"", "SESSION=SESSION"}:
        hint += "（当前 SESSION Cookie 仍为占位值，自动发布/查询通常无法生效。）"
    return {"ok": False, "message": hint, **(last or {})}


def publish_workflow(
    *,
    proc_id: str,
    proc_key: str,
    form_id: str | None = None,
    app_id: str | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """发布流程到运行时（工作台发起/审批依赖此步骤）。"""
    settings = _sync_settings_tenant(settings or get_settings())
    token = _resolve_token(token, settings)
    if not token:
        return {"ok": False, "message": "未配置 token，跳过发布"}

    base = settings.shenbi_base_url.rstrip("/")
    headers = _api_headers(token, settings=settings)
    tenant_id = settings.shenbi_tenant_id
    app = app_id or settings.shenbi_app_id
    bodies: list[dict[str, Any]] = [
        {
            "procId": proc_id,
            "id": proc_id,
            "procKey": proc_key,
            "appId": app,
            "tenantId": tenant_id,
        },
        {"procId": proc_id, "procKey": proc_key, "appId": app, "tenantId": tenant_id},
        {"id": proc_id, "procKey": proc_key, "tenantId": tenant_id},
        {"procKey": proc_key, "appId": app, "tenantId": tenant_id},
    ]
    if form_id:
        for body in bodies[:2]:
            body["formId"] = form_id

    last: dict[str, Any] = {}
    paths = (PUBLISH_PATH, *PUBLISH_ALT_PATHS)
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            for path in paths:
                url = f"{base}{path}"
                for body in bodies:
                    resp = client.post(url, json=body, headers=headers)
                    try:
                        payload = resp.json()
                    except Exception:
                        payload = {"raw": resp.text}
                    code = payload.get("code") if isinstance(payload, dict) else None
                    data = payload.get("data") if isinstance(payload, dict) else None
                    last = {
                        "endpoint": path,
                        "code": code,
                        "data": data,
                        "body": payload,
                    }
                    if code in (401, "401"):
                        continue
                    ok = code in SUCCESS_CODES and _response_data_ok(data)
                    message = _explain_api_code(payload) if isinstance(payload, dict) else str(payload)
                    if code in SUCCESS_CODES and not _response_data_ok(data):
                        if session_cookie_configured(settings):
                            message = (
                                "发布接口返回 code=200 但 data 为空，流程未发布到运行时。"
                                "请确认 SESSION Cookie 与 fighter-auth-token 来自同一浏览器会话。"
                            )
                        else:
                            message = (
                                "发布接口返回 code=200 但 data 为空，流程可能未发布到运行时。"
                                "请在流程写入页侧边栏填写浏览器 DevTools 中的完整 SESSION Cookie 后重新写入。"
                            )
                    if ok:
                        return {
                            "ok": True,
                            "code": code,
                            "data": data,
                            "message": message or "流程已发布到运行时",
                            "body": payload,
                            **last,
                        }
                # 部分环境支持 GET 发布
                get_url = f"{url}?procId={proc_id}&procKey={proc_key}&appId={app}"
                if form_id:
                    get_url += f"&formId={form_id}"
                resp = client.get(get_url, headers=headers)
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"raw": resp.text}
                code = payload.get("code") if isinstance(payload, dict) else None
                data = payload.get("data") if isinstance(payload, dict) else None
                last = {"endpoint": f"GET {path}", "code": code, "data": data, "body": payload}
                if code in SUCCESS_CODES and _response_data_ok(data):
                    return {
                        "ok": True,
                        "code": code,
                        "data": data,
                        "message": "流程已发布到运行时（GET）",
                        "body": payload,
                        **last,
                    }
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"发布请求连接失败：{exc}"}

    code = last.get("code")
    data = last.get("data")
    payload = last.get("body") if isinstance(last.get("body"), dict) else {}
    ok = code in SUCCESS_CODES and _response_data_ok(data)
    message = _explain_api_code(payload) if isinstance(payload, dict) else str(payload)
    if code in SUCCESS_CODES and not _response_data_ok(data):
        if session_cookie_configured(settings):
            message = (
                "发布接口返回 code=200 但 data 为空，流程未发布到运行时。"
                "请确认 SESSION Cookie 与 fighter-auth-token 来自同一浏览器会话。"
            )
        else:
            message = (
                "发布接口返回 code=200 但 data 为空，流程可能未发布到运行时。"
                "请在流程写入页侧边栏填写浏览器 DevTools 中的完整 SESSION Cookie 后重新写入。"
            )
    elif code in (401, "401"):
        message = (
            "发布失败：登录信息失效。"
            "请确认 fighter-auth-token 有效，并在侧边栏填写与 token 同一会话的 SESSION Cookie。"
        )
    return {
        "ok": ok,
        "code": code,
        "data": data,
        "message": message,
        "body": payload,
        **last,
    }


def check_shenbi_auth(*, settings: Settings | None = None, token: str | None = None) -> dict:
    """检测 token 是否有效，并探测 model-save 是否可用。"""
    settings = settings or get_settings()
    token = _resolve_token(token, settings)
    result = {
        "token_present": bool(token),
        "auth_ok": False,
        "auth_code": None,
        "auth_message": "",
        "save_probe_code": None,
        "save_probe_message": "",
        "save_probe_body_len": None,
    }
    if not token:
        result["auth_message"] = "未配置 token"
        return result

    base = settings.shenbi_base_url.rstrip("/")
    headers = _api_headers(token, settings=settings)
    probe_bytes = serialize_save_payload(
        {
            "id": None,
            "formModelJson": None,
            "formModel": {},
            "wfSimpleProc": {},
            "wfSimpleTaskInfo": {},
        }
    )
    try:
        with _http_client(settings=settings, timeout=30.0) as client:
            list_resp = client.post(
                f"{base}/fighter-baida/api/flow-simple/proc/page",
                json={"pageNum": 1, "pageSize": 1},
                headers=headers,
            )
            save_resp = client.post(
                f"{base}{MODEL_SAVE_PATH}",
                content=probe_bytes,
                headers=headers,
            )
    except httpx.ConnectError as exc:
        result["auth_message"] = f"连接失败：{exc}"
        return result

    for label, resp in (("auth", list_resp), ("save", save_resp)):
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        code = body.get("code") if isinstance(body, dict) else None
        msg = _explain_api_code(body) if isinstance(body, dict) else str(body)
        if label == "auth":
            result["auth_code"] = code
            result["auth_message"] = msg
            result["auth_ok"] = code in SUCCESS_CODES and code not in (401, "401")
        else:
            result["save_probe_code"] = code
            result["save_probe_message"] = msg
            result["save_probe_body_len"] = len(save_resp.request.content or b"")
    probe = probe_publish_capability(settings=settings, token=token)
    result["publish_ok"] = probe.get("publish_ok")
    result["runtime_ok"] = probe.get("runtime_ok")
    result["publish_message"] = probe.get("message") or (
        "未配置有效 SESSION Cookie；写入后请在侧边栏填写 Cookie 并点击「批量发布流程」"
    )
    return result


def republish_workflow_from_registry(
    workflow_name: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """仅重新绑定菜单并发布已有 registry 流程到运行时（修复「查无该流程定义信息」）。"""
    settings = _sync_settings_tenant(settings or get_settings())
    registry = load_workflow_registry(workflow_name, settings=settings)
    if not registry_app_verified(registry):
        raise ShenbiApiError(f"未找到已验证 registry：{workflow_name}")
    app_id = str(registry.get("app_id") or "")
    form_id = str(registry.get("form_id") or "")
    proc_id = str(registry.get("proc_id") or "")
    proc_key = str(registry.get("proc_key") or registry.get("table_name") or "")
    app_mc = str(registry.get("app_mc") or "")
    if not all([app_id, form_id, proc_id, proc_key]):
        raise ShenbiApiError(f"registry 缺少 app/form/proc 信息：{workflow_name}")

    menu_steps = rebind_flow_menus_for_workflow_aliases(
        workflow_name=workflow_name,
        primary_app_id=app_id,
        title=app_mc or workflow_name,
        form_id=form_id,
        proc_id=proc_id,
        proc_key=proc_key,
        registry=registry,
        settings=settings,
        token=token,
    )
    publish_result = publish_workflow(
        proc_id=proc_id,
        proc_key=proc_key,
        form_id=form_id,
        app_id=app_id,
        settings=settings,
        token=token,
    )
    runtime_check = verify_runtime_proc(
        proc_key=proc_key,
        proc_id=proc_id,
        app_id=app_id,
        settings=settings,
        token=token,
    )
    if not runtime_check.get("ok"):
        hint = str(runtime_check.get("message") or publish_result.get("message") or "流程发布到运行时失败")
        if not session_cookie_configured(settings):
            hint = (
                "发布/运行时校验需要浏览器 SESSION Cookie（不能为 SESSION=SESSION 占位值）。"
                "请登录 ym.zhiduo.net 后，从 DevTools → Network 复制完整 Cookie 到流程写入页侧边栏，再点「批量发布流程」。"
            )
        raise ShenbiApiError(
            hint,
            body={"publish": publish_result, "runtime_check": runtime_check, "menu_steps": menu_steps},
        )
    return {
        "workflow_name": workflow_name,
        "app_id": app_id,
        "proc_id": proc_id,
        "proc_key": proc_key,
        "menu_steps": menu_steps,
        "publish": publish_result,
        "runtime_check": runtime_check,
    }


def list_verified_registries(*, settings: Settings | None = None) -> list[tuple[str, dict]]:
    """扫描 output/workflows/shenbi 下全部已验证 registry。"""
    settings = settings or get_settings()
    root = settings.output_dir / "workflows" / "shenbi"
    if not root.is_dir():
        return []
    items: list[tuple[str, dict]] = []
    for path in sorted(root.glob("*_registry.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not registry_app_verified(data):
            continue
        name = str(data.get("workflow_name") or path.stem.replace("_registry", ""))
        items.append((name, data))
    return items


def republish_all_registries(
    *,
    settings: Settings | None = None,
    token: str | None = None,
    on_progress: Any | None = None,
) -> dict:
    """批量发布本地 registry 流程到运行时（修复已写入但未发布的流程）。"""
    settings = _sync_settings_tenant(settings or get_settings())
    registries = list_verified_registries(settings=settings)
    total = len(registries)
    succeeded: list[dict] = []
    failed: list[dict] = []
    for idx, (name, _reg) in enumerate(registries, start=1):
        if on_progress:
            on_progress(idx - 1, total, name)
        try:
            result = republish_workflow_from_registry(name, settings=settings, token=token)
            succeeded.append({"workflow_name": name, **result})
        except Exception as exc:
            failed.append({"workflow_name": name, "error": str(exc)})
        if on_progress:
            on_progress(idx, total, name)
    return {
        "total": total,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "items_ok": succeeded,
        "items_fail": failed,
    }


def _remote_task_tree_populated(remote_types: dict | None, *, min_usertask: int = 8) -> bool:
    if not remote_types:
        return False
    if int(remote_types.get("ROUTE") or 0) >= 3:
        return True
    if int(remote_types.get("USERTASK") or 0) >= min_usertask:
        return True
    if int(remote_types.get("BRANCHTASK") or 0) >= 5:
        return True
    return False


def assess_complex_write_outcome(
    *,
    save_response: dict,
    local_model: dict,
    persisted_check: dict,
) -> dict:
    """复杂流程写入结论。model-get 常不回传 ROUTE 节点，不能单凭其否定写入成功。"""
    local_types = verify_model_integrity(local_model).get("task_types") or {}
    local_routes = int(local_types.get("ROUTE") or 0)
    remote_types = persisted_check.get("remote_task_types") or {}
    remote_routes = int(remote_types.get("ROUTE") or 0)

    if _response_looks_like_shell(save_response, min_route_branches=4):
        return {
            "ok": False,
            "level": "error",
            "message": "model-save 响应仍为空壳环节树，请在设计器确认",
        }

    if persisted_check.get("ok") or _remote_task_tree_populated(remote_types):
        return {
            "ok": True,
            "level": "ok",
            "message": str(persisted_check.get("message") or "平台回读正常"),
        }

    if local_routes >= 3 and local_model_has_task_tree(local_model):
        return {
            "ok": True,
            "level": "warn",
            "message": (
                f"model-get 未回传完整环节树（平台 ROUTE×{remote_routes}，本地 ROUTE×{local_routes}）。"
                "写入已成功，请在设计器确认流程结构。"
            ),
        }

    return {
        "ok": False,
        "level": "error",
        "message": (
            "复杂流程写入后平台环节异常（"
            f"平台 ROUTE×{remote_routes}，本地 ROUTE×{local_routes}）"
        ),
    }


def response_has_task_tree(response: dict) -> bool:
    """model-save 响应是否包含可解析的环节树。"""
    if not isinstance(response, dict):
        return False
    data = response.get("data")
    if not isinstance(data, dict):
        return False
    task = _decode_field(data.get("wfSimpleTaskInfo"))
    return isinstance(task, dict) and bool(task.get("type"))


def write_result_tree_confirmed(
    write_result: dict,
    *,
    local_model: dict | None = None,
) -> bool:
    """写入结果中 model-save 是否回传与本地一致的完整环节树。"""
    from core.workflow.flow_topology import local_task_tree_intact, task_tree_degraded

    local_tree = None
    if isinstance(local_model, dict):
        local_tree = local_model.get("wfSimpleTaskInfo")
    for step in write_result.get("results") or []:
        if step.get("step") not in {"表单与流程", "环节树补写"}:
            continue
        response = step.get("response")
        if not response_has_task_tree(response or {}):
            continue
        remote = task_tree_from_save_response(response)
        if not isinstance(remote, dict):
            continue
        if isinstance(local_tree, dict) and local_task_tree_intact(local_tree):
            if task_tree_degraded(remote, local_tree):
                continue
        return True
    return False


def local_model_has_task_tree(model: dict) -> bool:
    task = model.get("wfSimpleTaskInfo")
    return isinstance(task, dict) and bool(task.get("type"))


def _maybe_parse_json(value: Any) -> Any:
    return _decode_field(value)


class ShenbiApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def save_proc_model(payload: dict, *, settings: Settings | None = None, token: str | None = None) -> dict:
    """调用 model-save 接口保存流程模型。"""
    settings = _sync_settings_tenant(settings or get_settings())
    token = _resolve_token(token, settings)
    if not token:
        raise ShenbiApiError("未配置神笔平台 token（fighter-auth-token）")

    normalize_model_tenant_ids(payload, settings.shenbi_tenant_id)
    base = settings.shenbi_base_url.rstrip("/")
    url = f"{base}{MODEL_SAVE_PATH}"
    headers = _api_headers(token, settings=settings)
    api_payload = prepare_save_payload(payload)
    body_bytes = _compact_json(api_payload).encode("utf-8")
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _http_client(settings=settings) as client:
            resp = client.post(url, content=body_bytes, headers=headers)
    except httpx.ConnectError as exc:
        cause = exc.__cause__
        if cause and "CERTIFICATE_VERIFY_FAILED" in str(cause):
            raise ShenbiApiError(
                "HTTPS 证书校验失败（证书可能已过期）。"
                "请在 .env 设置 SHENBI_SSL_VERIFY=false 临时跳过校验，"
                "或联系神笔平台/cloudschool.cn 续期 SSL 证书。"
            ) from exc
        raise ShenbiApiError(f"无法连接神笔平台：{exc}") from exc

    try:
        body = resp.json()
    except Exception:
        body = resp.text

    if resp.status_code >= 400:
        raise ShenbiApiError(
            f"神笔 API 请求失败 HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )

    if isinstance(body, dict):
        code = body.get("code")
        if code not in SUCCESS_CODES:
            (log_dir / "shenbi_last_response.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "request_body_len": len(body_bytes),
                        "request_keys": list(api_payload.keys()),
                        "wfSimpleDefaultModel_len": len(api_payload.get("wfSimpleDefaultModel") or ""),
                        "response": body,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise ShenbiApiError(
                f"神笔 API 返回错误（code={code}）：{_explain_api_code(body)}",
                status_code=resp.status_code,
                body=body,
            )
    return body if isinstance(body, dict) else {"raw": body}


def merge_save_response_preserve_tree(payload: dict, response: dict) -> dict:
    """合并 save 响应；若平台回传环节树明显缩水的，保留本地树仅合并 id。"""
    merged = merge_save_response(payload, response)
    local_task = merged.get("wfSimpleTaskInfo")
    if not isinstance(local_task, dict):
        return merged
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    parsed_task = _maybe_parse_json(data.get("wfSimpleTaskInfo"))
    if not isinstance(parsed_task, dict) or not parsed_task.get("type"):
        return merged
    local_n = sum(count_task_types(local_task).values())
    remote_n = sum(count_task_types(parsed_task).values())
    if remote_n < max(local_n // 2, 5):
        merge_platform_task_ids(local_task, parsed_task)
        sync_from_property_list_task_ids(local_task)
        merged["wfSimpleTaskInfo"] = local_task
    return merged


def merge_save_response(payload: dict, response: dict) -> dict:
    """将保存响应中的 id 合并回 payload，供后续步骤使用。"""
    merged = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(response, dict):
        return merged

    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        return merged

    for key in ("id",):
        if key in data and data[key] is not None:
            merged[key] = data[key]

    local_fm = merged.get("formModel") if isinstance(merged.get("formModel"), dict) else None
    parsed_fm = _maybe_parse_json(data.get("formModel"))
    if isinstance(local_fm, dict) and isinstance(parsed_fm, dict):
        if parsed_fm.get("formId"):
            local_fm["formId"] = parsed_fm["formId"]
        merged["formModel"] = local_fm
    elif isinstance(parsed_fm, dict):
        merged["formModel"] = parsed_fm

    local_proc = merged.get("wfSimpleProc") if isinstance(merged.get("wfSimpleProc"), dict) else None
    parsed_proc = _maybe_parse_json(data.get("wfSimpleProc"))
    if isinstance(local_proc, dict) and isinstance(parsed_proc, dict):
        for field in ("id", "fromId", "procKey", "procName"):
            if parsed_proc.get(field):
                local_proc[field] = parsed_proc[field]
        merged["wfSimpleProc"] = local_proc
    elif isinstance(parsed_proc, dict):
        merged["wfSimpleProc"] = parsed_proc

    local_task = merged.get("wfSimpleTaskInfo")
    parsed_task = _maybe_parse_json(data.get("wfSimpleTaskInfo"))
    if isinstance(local_task, dict) and local_task.get("type"):
        if isinstance(parsed_task, dict) and parsed_task.get("type"):
            merge_platform_task_ids(local_task, parsed_task)
        merged["wfSimpleTaskInfo"] = local_task
    elif isinstance(parsed_task, dict) and parsed_task.get("type"):
        merged["wfSimpleTaskInfo"] = parsed_task

    return sync_form_model_json(merged)
