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
from core.workflow.shenbi_builder import finalize_workflow_model, workflow_registry_slug

MODEL_SAVE_PATH = "/fighter-baida/api/flow-simple/proc/model-save"
APP_SAVE_PATH = "/fighter-baida/api/baida/tBaidaApp/saveOrUpdate"
APP_QUERY_LIST_PATH = "/fighter-baida/api/baida/tBaidaApp/queryList"
APP_MENU_SAVE_PATH = "/fighter-baida/api/baida/tBaidaMenu/saveOrUpdate"
APP_MENU_QUERY_PATH = "/fighter-baida/api/baida/tBaidaMenu/queryList"
PUBLISH_PATH = "/fighter-baida/api/flow-simple/proc/publish"
DEFAULT_FLOW_MENU_ICON = "el-icon-_condition"
FLOW_MENU_TYPE = 2  # 流程表单页（参考 L_测试3 → 测试流程3）
DEFAULT_APP_ICON = (
    "https://www.cloudschool.cn/fastdfs/group1/M00/00/1B/rBMqYmdQDQmAOkrRAAAKAl_aFqI729.png"
)
DEFAULT_APP_CATEGORY = "3"  # 服务（与样例 yydl 一致）
MODEL_FIELDS = ("id", "formModelJson", "formModel", "wfSimpleProc", "wfSimpleTaskInfo")
SUCCESS_CODES = {None, 0, 200, "0", "200", "success"}
SHENBI_ORIGIN = "https://www.cloudschool.cn"
SHENBI_REFERER = "https://www.cloudschool.cn/baidaForm/"
SHENBI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def _resolve_proxy(settings: Settings) -> str | None:
    proxy = (settings.shenbi_proxy or settings.oa_proxy or "").strip()
    return proxy or None


def _api_headers(token: str, *, settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    headers = {
        "fighter-auth-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": SHENBI_ORIGIN,
        "Referer": SHENBI_REFERER,
        "User-Agent": SHENBI_USER_AGENT,
    }
    cookie = (settings.shenbi_session_cookie or "").strip()
    if cookie:
        headers["Cookie"] = cookie
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

    updated = copy.deepcopy(model)
    form_model = updated.setdefault("formModel", {})
    proc = updated.setdefault("wfSimpleProc", {})
    form_model["formId"] = form_id
    proc["fromId"] = form_id
    proc["id"] = proc_id
    proc["complexProcId"] = None
    if updated.get("wfSimpleTaskInfo"):
        updated["wfSimpleTaskInfo"] = _sync_task_tree_proc_ids(
            updated["wfSimpleTaskInfo"], proc_id=proc_id
        )
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
    settings: Settings | None = None,
) -> dict[str, Any]:
    """组装 tBaidaApp/saveOrUpdate 请求体；mc = 流程显示名。"""
    from core.workflow.shenbi_builder import WORKFLOW_CATALOG, display_workflow_name

    settings = settings or get_settings()
    mc = display_workflow_name(workflow_name)
    meta = WORKFLOW_CATALOG.get(workflow_name, {})
    form_slug = str(meta.get("form_slug") or workflow_name)
    payload: dict[str, Any] = {
        "icons": DEFAULT_APP_ICON,
        "mc": mc,
        "sfqy": "1",
        "template": 0,
        "yyjj": f"野马智能交付工作台自动创建 · 关联 OA 表单 {form_slug}",
        "yydl": DEFAULT_APP_CATEGORY,
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
    token = (token or settings.shenbi_api_token or "").strip()
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
    token = (token or settings.shenbi_api_token or "").strip()
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


def find_app_id_by_mc(
    mc: str,
    *,
    settings: Settings | None = None,
    token: str | None = None,
) -> str | None:
    """按应用名称精确匹配 appId。"""
    target = (mc or "").strip()
    if not target:
        return None
    for app in query_app_list(settings=settings, token=token):
        if (app.get("mc") or "").strip() == target and app.get("id"):
            return str(app["id"])
    return None


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
    token = (token or settings.shenbi_api_token or "").strip()
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
        menu_title = str(menu.get("title") or title).strip() or title
        extend = menu.get("extendInfo") if isinstance(menu.get("extendInfo"), dict) else {}
        bound_form, bound_proc = _menu_bound_ids(menu)
        needs_rebind = bound_form != form_id or bound_proc != proc_id
        payload = build_flow_menu_payload(
            app_id=app_id,
            title=menu_title,
            form_id=form_id,
            proc_id=proc_id,
            proc_key=proc_key,
            menu_id=str(menu["menuId"]) if menu.get("menuId") else None,
            sort_number=int(menu.get("sortNumber") or 5),
        )
        if needs_rebind or not menu.get("menuId"):
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
    token = (token or settings.shenbi_api_token or "").strip()
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
    token = (token or settings.shenbi_api_token or "").strip()
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
    if existing and not needs_rebind:
        mode = "existing"
    elif existing:
        mode = "rebind"
    else:
        mode = "create"
    response = save_app_menu(payload, settings=settings, token=token)
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
) -> tuple[str, dict]:
    """调用 tBaidaApp/saveOrUpdate：优先复用同名应用，否则新建。"""
    from core.workflow.shenbi_builder import display_workflow_name

    settings = settings or get_settings()
    registry = registry or load_workflow_registry(workflow_name, settings=settings)
    mc = display_workflow_name(workflow_name)
    mode = "create"
    app_id: str | None = None

    if registry_app_verified(registry) and registry.get("app_id"):
        app_id = str(registry["app_id"])
        mode = "update"
    else:
        app_id = find_app_id_by_mc(mc, settings=settings, token=token)
        if app_id:
            mode = "reuse"

    payload = build_app_save_payload(workflow_name, app_id=app_id, settings=settings)

    try:
        response = save_or_update_app(payload, settings=settings, token=token)
    except ShenbiApiError as exc:
        if mode == "create" and _app_name_exists_error(exc):
            app_id = find_app_id_by_mc(mc, settings=settings, token=token)
            if not app_id:
                raise
            payload = build_app_save_payload(workflow_name, app_id=app_id, settings=settings)
            response = save_or_update_app(payload, settings=settings, token=token)
            mode = "reuse"
        else:
            raise

    app_id = extract_app_id(response) or app_id
    if not app_id:
        raise ShenbiApiError("创建应用后未返回 appId", body=response)
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
    """生成 JSON 后注入已创建流程的平台 ID，相同 workflow 走更新而非新建。"""
    form_id, proc_id, app_id = resolve_platform_ids(payloads, workflow_name, settings=settings)
    flow_kind = _workflow_flow_kind(workflow_name)
    if flow_kind == "complex_recruitment":
        # 复杂招聘流程每次全量新建表单/流程，仅复用 appId 绑定同一应用
        if not app_id:
            return payloads
        merged = copy.deepcopy(payloads)
        model = merged.get("model")
        if isinstance(model, dict):
            model.setdefault("wfSimpleProc", {})["appId"] = app_id
            form_model = model.setdefault("formModel", {})
            form_model.pop("formId", None)
            proc = model["wfSimpleProc"]
            proc.pop("id", None)
            proc.pop("fromId", None)
            strip_task_create_ids(model.get("wfSimpleTaskInfo"))
            merged["model"] = finalize_workflow_model(model)
        return merged
    if not form_id or not proc_id:
        return payloads
    merged = copy.deepcopy(payloads)
    model = merged.get("model")
    if isinstance(model, dict):
        updated = apply_saved_ids(model, form_id=form_id, proc_id=proc_id)
        if app_id:
            updated.setdefault("wfSimpleProc", {})["appId"] = app_id
        registry = load_workflow_registry(workflow_name, settings=settings) or {}
        proc = updated.setdefault("wfSimpleProc", {})
        if registry.get("proc_key"):
            proc["procKey"] = registry["proc_key"]
        fm = updated.setdefault("formModel", {})
        proc_key = str(registry.get("proc_key") or proc.get("procKey") or "")
        table_name = str(registry.get("table_name") or fm.get("tableName") or proc_key)
        if proc_key and table_name != proc_key:
            table_name = proc_key
        if table_name:
            fm["tableName"] = table_name
            proc["procKey"] = proc_key or table_name
        task_ids = registry.get("task_id_by_key")
        if isinstance(task_ids, dict) and updated.get("wfSimpleTaskInfo"):
            apply_task_id_by_key(updated["wfSimpleTaskInfo"], task_ids)
        merged["model"] = finalize_workflow_model(updated)
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


def _workflow_flow_kind(workflow_name: str) -> str:
    from core.workflow.shenbi_builder import WORKFLOW_CATALOG

    return str((WORKFLOW_CATALOG.get(workflow_name) or {}).get("flow_kind") or "linear")


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
    flow_kind: str,
    strip_complex_tasks: bool = False,
) -> dict:
    """组装 model-save 请求体。

    复杂流程覆盖空壳时须 strip 环节 id 且 formModelJson=null；仅改权限时可保留环节 id。
    """
    if is_update:
        body = apply_saved_ids(
            sync_form_model_json(model),
            form_id=str(form_id or ""),
            proc_id=str(proc_id or ""),
        )
    else:
        body = strip_create_ids(sync_form_model_json(model))
    body.setdefault("wfSimpleProc", {})["appId"] = app_id
    if flow_kind == "complex_recruitment":
        if strip_complex_tasks or not is_update:
            strip_task_create_ids(body.get("wfSimpleTaskInfo"))
        body = finalize_workflow_model(body)
        if strip_complex_tasks or not is_update:
            body["formModelJson"] = None
        return body
    return finalize_workflow_model(body)


def save_workflow_models(
    payloads: dict,
    *,
    workflow_name: str,
    settings: Settings | None = None,
    token: str | None = None,
    recreate: bool = False,
) -> dict:
    """先 ensure 应用，再 model-save 创建/更新表单与流程。

    复杂招聘流程：优先覆盖应用菜单/designer 当前 proc（清空环节 id 后全量写入）；
    线性流程：registry 有效时 update，formId 失效时自动重建。
    """
    settings = settings or get_settings()
    flow_kind = _workflow_flow_kind(workflow_name)
    registry = load_workflow_registry(workflow_name, settings=settings)
    legacy = is_legacy_registry(registry, settings=settings)
    if recreate:
        form_id, proc_id = None, None
        is_update = False
    else:
        form_id, proc_id, _ = resolve_platform_ids(payloads, workflow_name, settings=settings)
        is_update = bool(form_id and proc_id)

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
    )
    results.append(app_step)

    complex_target_mode = ""
    if flow_kind == "complex_recruitment" and not recreate:
        form_id, proc_id = None, None
        is_update = False
        complex_target_mode = "create"
        results.append(
            {
                "step": "复杂流程",
                "mode": "create",
                "detail": (
                    "复杂招聘流程全量新建表单/流程并绑定菜单"
                    "（平台 update 无法恢复已塌陷的环节树）"
                ),
            }
        )

    model = finalize_workflow_model(copy.deepcopy(model))
    model.setdefault("wfSimpleProc", {})["appId"] = app_id

    auto_recreate = False
    response: dict = {}
    complex_target_mode = complex_target_mode if flow_kind == "complex_recruitment" else ""
    strip_complex_tasks = (
        flow_kind == "complex_recruitment"
        and (not is_update or complex_target_mode.startswith("overwrite"))
    )

    if is_update:
        try:
            body = _prepare_model_save_body(
                model,
                app_id=app_id,
                is_update=True,
                form_id=form_id,
                proc_id=proc_id,
                flow_kind=flow_kind,
                strip_complex_tasks=strip_complex_tasks,
            )
            response = save_proc_model(body, settings=settings, token=token)
            save_hint = assess_model_save_response(response, local_model=body)
            results.append(
                {
                    "step": "表单与流程",
                    "mode": "update",
                    "response": response,
                    "detail": save_hint.get("message"),
                }
            )
            if flow_kind == "complex_recruitment" and _response_looks_like_shell(
                response, min_route_branches=4
            ):
                auto_recreate = True
                results.append(
                    {
                        "step": "自动重建",
                        "mode": "create",
                        "detail": "检测到平台仍为空壳环节树，将自动新建表单/流程并重新绑定菜单",
                    }
                )
        except ShenbiApiError as exc:
            if _is_stale_registry_save_error(exc):
                auto_recreate = True
                is_update = False
                results.append(
                    {
                        "step": "自动重建",
                        "mode": "create",
                        "detail": f"registry 表单/流程已失效，将全量新建：{exc.args[0]}",
                    }
                )
            else:
                raise

    if not is_update or auto_recreate or recreate:
        body = _prepare_model_save_body(
            model,
            app_id=app_id,
            is_update=False,
            form_id=None,
            proc_id=None,
            flow_kind=flow_kind,
        )
        response = save_proc_model(body, settings=settings, token=token)
        save_hint = assess_model_save_response(response, local_model=body)
        results.append(
            {
                "step": "表单与流程" if not (auto_recreate or recreate) else "表单与流程（重建）",
                "mode": "create",
                "response": response,
                "detail": save_hint.get("message"),
            }
        )

    model = finalize_workflow_model(merge_save_response_preserve_tree(model, response))
    form_id, proc_id = extract_platform_ids(model)
    if not form_id or not proc_id:
        raise ShenbiApiError("保存后未返回 formId / procId", body=response)

    if flow_kind == "complex_recruitment" and _complex_task_tree_collapsed(
        model.get("wfSimpleTaskInfo") if isinstance(model.get("wfSimpleTaskInfo"), dict) else None
    ):
        raise ShenbiApiError(
            "复杂流程新建后环节树仍不完整（仅 STARTTASK），请检查 token 或稍后重试一键写入"
        )

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
    menu_steps = rebind_all_flow_menus(
        app_id=app_id,
        title=menu_title,
        form_id=form_id,
        proc_id=proc_id,
        proc_key=proc_key,
        settings=settings,
        token=token,
    )
    results.extend(menu_steps)

    publish_result = publish_workflow(
        proc_id=proc_id,
        proc_key=proc_key,
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

    if flow_kind == "complex_recruitment":
        write_outcome = assess_complex_write_outcome(
            save_response=response,
            local_model=model,
            persisted_check=persisted_check,
        )
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
        "is_update": is_update,
        "form_id": form_id,
        "proc_id": proc_id,
        "proc_key": proc_key,
        "app_id": app_id,
        "app_mc": app_mc,
        "publish": publish_result,
        "runtime_check": runtime_check,
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
    token = (token or settings.shenbi_api_token or "").strip()
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
                return {
                    "ok": bool(local_types),
                    "message": "平台 model-get data 为空，以本地模型为准",
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
    token = (token or settings.shenbi_api_token or "").strip()
    if not token:
        return {"ok": False, "message": "未配置 token"}

    base = settings.shenbi_base_url.rstrip("/")
    headers = _api_headers(token, settings=settings)
    body: dict[str, Any] = {
        "procKey": proc_key,
        "appId": app_id or settings.shenbi_app_id,
    }
    if proc_id:
        body["procId"] = proc_id
        body["id"] = proc_id

    endpoints = (
        "/fighter-baida/api/flow-simple/proc/getProcDef",
        "/fighter-baida/api/flow-simple/proc/getByKey",
        "/fighter-baida/api/flow-simple/proc/detail",
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
    app_id: str | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> dict:
    """发布流程到运行时（工作台发起/审批依赖此步骤）。"""
    settings = settings or get_settings()
    token = (token or settings.shenbi_api_token or "").strip()
    if not token:
        return {"ok": False, "message": "未配置 token，跳过发布"}

    base = settings.shenbi_base_url.rstrip("/")
    url = f"{base}{PUBLISH_PATH}"
    headers = _api_headers(token, settings=settings)
    body = {
        "procId": proc_id,
        "id": proc_id,
        "procKey": proc_key,
        "appId": app_id or settings.shenbi_app_id,
        "tenantId": settings.shenbi_tenant_id,
    }
    try:
        with _http_client(settings=settings, timeout=60.0) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"发布请求连接失败：{exc}"}

    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text}
    code = payload.get("code") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    ok = code in SUCCESS_CODES and _response_data_ok(data)
    message = _explain_api_code(payload) if isinstance(payload, dict) else str(payload)
    if code in SUCCESS_CODES and not _response_data_ok(data):
        message = (
            "发布接口返回 code=200 但 data 为空，流程可能未发布到运行时。"
            "请在设计器手动点击「发布」，或配置真实 SESSION Cookie 后重试。"
        )
    return {
        "ok": ok,
        "code": code,
        "data": data,
        "message": message,
        "body": payload,
    }


def check_shenbi_auth(*, settings: Settings | None = None, token: str | None = None) -> dict:
    """检测 token 是否有效，并探测 model-save 是否可用。"""
    settings = settings or get_settings()
    token = (token or settings.shenbi_api_token or "").strip()
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
    return result


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
    settings = settings or get_settings()
    token = (token or settings.shenbi_api_token or "").strip()
    if not token:
        raise ShenbiApiError("未配置神笔平台 token（fighter-auth-token）")

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
