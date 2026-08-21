"""神笔平台工作流 JSON 构建：基础配置 / 表单 / 流程。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config.settings import Settings, get_settings
from core.config.shenbi_environments import get_shenbi_config
from core.form.form_store import load_form
from core.form.name_utils import (
    clamp_platform_field_name,
    display_app_name,
    display_form_name,
    display_workflow_name,
    is_valid_ascii_prop,
    normalize_workflow_name,
    string_similarity,
    task_key_from_name,
)
from core.workflow.flow_topology import is_expense_task_tree_source, is_recruitment_task_tree_source
from core.workflow.guide_dag_compiler import compile_task_tree_from_guide, summarize_recruitment_branch_plans
from core.workflow.expense_flow_compiler import summarize_expense_branch_bindings
from core.workflow.complex_flow_builder import (
    iter_all_tasks,
    recruitment_branch_form_fields,
    expense_branch_form_fields,
    foreign_trade_expense_branch_form_fields,
    ensure_fybm_select_field,
    amount_branch_form_fields,
    expense_type_branch_form_fields,
    subsidiary_branch_form_fields,
    branch_field_props_in_tree,
    JEFD_FIELD,
    FYLX_FIELD,
    ZGS_FIELD,
    SLYZ_FIELD,
)
from core.workflow.flow_semantics import (
    SemanticStep,
    SelfSelectFieldSpec,
    collect_self_select_field_specs,
    collect_self_select_cc_specs,
    guide_nodes_to_semantic_steps,
    semantic_steps_summary,
)
from core.workflow.result_store import load_result, resolve_parsed_flow
from core.workflow.workflow_catalog import WORKFLOW_CATALOG, get_catalog_hints, get_catalog_meta
from schemas.form import RawFormRecord
from schemas.workflow import WorkflowFlowResult, WorkflowNodeItem

APPROVAL_GROUP_LABEL = "审批意见"
FORM_INFO_GROUP_LABEL = "表单信息"
# 神笔「后端用户 / 姓名」采集配置；样例流程共用同一 resourceId，是否跨应用冲突待实测。
INITIATOR_RESOURCE_ID = "ce25c09f8f944aa4bec9df6af65b233a"

SYSTEM_READONLY_PROPS = frozenset(
    {"id", "processInstanceId", "taskId", "creator", "createTime", "lastUpdator", "lastUpdateTime", "orgId", "processCode", "fqr"}
)

# 表单设计器 display=false 的系统字段：权限列表中不包含（手工 save 规律）
HIDDEN_FORM_FIELD_PROPS = frozenset(
    {"id", "processInstanceId", "taskId", "creator", "createTime", "lastUpdator", "lastUpdateTime"}
)

# 职务级别 zwjb：STARTTASK 可填；USERTASK 与表单信息其他字段一致为只读（神笔设计器规律）

# 静态样例 catalog 定义于 workflow_catalog.py，此处 re-export 供测试与旧代码引用。

def workflow_registry_slug(workflow_name: str) -> str:
    slug = normalize_workflow_name(workflow_name)
    if not slug:
        slug = hashlib.md5(workflow_name.encode()).hexdigest()[:12]
    return slug


def _registry_ids(registry: dict | None) -> dict[str, str]:
    if not registry:
        return {}
    out: dict[str, str] = {}
    for key in ("app_id", "form_id", "proc_id", "proc_key", "table_name"):
        val = registry.get(key)
        if val:
            out[key] = str(val)
    return out

LABEL_PROP_MAP: dict[str, str] = {
    "发起人": "fqr",
    "部门": "orgId",
    "流程编号": "processCode",
}

# 常见 OA 业务字段 → 神笔 prop（须为 ASCII 且以字母开头）
BUSINESS_LABEL_PROP_MAP: dict[str, str] = {
    "用人部门": "yrbm",
    "招聘部门": "zpbm",
    "岗位名称": "gwmc",
    "职级": "zwjb",
    "职务级别": "zwjb",
    "编制人数": "bzrs",
    "在岗人数": "zgrs",
    "需招聘人数": "xzprs",
    "发起日期": "fqrq",
    "需求原因": "xqyy",
    "工作职责": "gzzz",
    "性别": "xb",
    "学历": "xl",
    "年龄": "nl",
    "民族": "mz",
    "专业": "zy",
    "证书": "zs",
    "外语水平": "wysp",
    "户口（护照）所在地区": "hkszdq",
    "户口(护照)所在地区": "hkszdq",
    "驾照": "jz",
    "性格": "xg",
    "工作经验": "gzjy",
    "采购合同号": "cghtbh",
    "采购数量": "cgsl",
    "采购发票日期": "cgfprq",
    "费用事由": "fysy",
    "报销金额": "bxje",
    "是否总裁办": "sfzcb",
    "金额分段": "jefd",
    "是否审计部": "sfsjb",
    "出纳类型": "cnlx",
    "其他要求": "qtyq",
}

_SKIP_BUSINESS_LABELS = frozenset(
    {"基本信息", "合同信息", "审阅信息", "采购合同", "新建", "复制", "删除", "审批环节"}
)
_JUNK_FIELD_LABEL_RE = re.compile(r"^数字\d+$")
_VALID_PROP_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    return uuid.uuid4().hex


def _initiator_column(*, used_props: set[str]) -> dict:
    used_props.add("fqr")
    return {
        "shieldProcessRecord": True,
        "resourceId": INITIATOR_RESOURCE_ID,
        "fieldName": "realname",
        "maxlength": 200,
        "labelslot": True,
        "display": True,
        "dataType": "varchar",
        "rules": [],
        "label": "发起人",
        "type": "input",
        "indb": True,
        "prop": "fqr",
        "disabled": True,
        "span": 12,
    }


def _approval_field_props(form_model: dict) -> set[str]:
    props: set[str] = set()
    for group in form_model.get("group") or []:
        if group.get("label") == APPROVAL_GROUP_LABEL:
            for col in group.get("column") or []:
                prop = col.get("prop")
                if prop:
                    props.add(str(prop))
    return props


def _normalize_form_permissions(model: dict) -> dict:
    """发起人环节：审批意见分组字段均为 readable（不可写）。"""
    updated = copy.deepcopy(model)
    form_model = updated.get("formModel")
    if not isinstance(form_model, dict):
        return updated

    for group in form_model.get("group") or []:
        if group.get("label") in (APPROVAL_GROUP_LABEL, "审批信息", "分组"):
            group["label"] = APPROVAL_GROUP_LABEL

    approval_props = _approval_field_props(form_model)

    def walk_task(node: dict | None) -> None:
        if not node:
            return
        if node.get("type") == "STARTTASK":
            props = node.setdefault("properties", {})
            if not isinstance(props, dict):
                return
            for fp in props.get("fromPropertyList") or []:
                if fp.get("fieldProp") in approval_props:
                    fp["operating"] = "readable"
        child = node.get("child")
        if isinstance(child, dict):
            walk_task(child)
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                walk_task(cond)

    walk_task(updated.get("wfSimpleTaskInfo"))
    return updated


def _build_flow_user_choose_column(*, label: str, prop: str) -> dict:
    """发起人自选：表单信息分组中的选人字段（与神笔样车合同备案表一致）。"""
    return {
        "shieldProcessRecord": True,
        "isImport": True,
        "idListName": "",
        "display": True,
        "dataType": "text",
        "rules": [],
        "label": label,
        "type": "flowUserChoose",
        "orgCodeList": [],
        "component": "flow-user-choose",
        "maxRows": 1,
        "indb": True,
        "orgOrRoleSort": "1",
        "prop": prop,
        "disabled": False,
        "nameListName": "",
        "roleCodeList": [],
        "selectType": 3,
        "placeholder": "请选择用户",
        "endPlaceholder": "",
        "isSelfTenant": False,
        "span": 12,
        "selfOrg": False,
    }


def _resolve_self_select_specs(
    specs: list[SelfSelectFieldSpec],
    form_info_cols: list[dict],
    *,
    used_props: set[str],
) -> list[SelfSelectFieldSpec]:
    """为自选规格补全 prop，并复用表单中已有同标签选人字段。"""
    label_to_col = {str(c.get("label") or ""): c for c in form_info_cols}
    resolved: list[SelfSelectFieldSpec] = []
    for spec in specs:
        existing = label_to_col.get(spec.field_label)
        if existing and str(existing.get("type") or "") == "flowUserChoose":
            prop = str(existing.get("prop") or spec.field_prop)
            if not is_valid_ascii_prop(prop):
                prop = label_to_prop(spec.field_label, used_props)
        elif spec.field_prop and is_valid_ascii_prop(spec.field_prop):
            prop = spec.field_prop
        else:
            prop = label_to_prop(spec.field_label, used_props)
        resolved.append(
            SelfSelectFieldSpec(
                field_label=spec.field_label,
                field_prop=prop,
                task_name=spec.task_name,
            )
        )
    return resolved


def _balance_half_row_spans(cols: list[dict]) -> None:
    """span=12 的字段为奇数个时，最后一个改为 span=24，避免半行留白导致错位。"""
    half_row = [c for c in cols if int(c.get("span") or 12) <= 12]
    if len(half_row) % 2 == 1 and half_row:
        half_row[-1]["span"] = 24


def _normalize_form_info_layout(form_info_cols: list[dict]) -> None:
    """表单信息分组：常规字段在前，flowUserChoose 选人字段统一排在末尾并平衡行宽。"""
    hidden = [c for c in form_info_cols if c.get("display") is False]
    visible = [c for c in form_info_cols if c.get("display") is not False]
    regular = [c for c in visible if str(c.get("type") or "") != "flowUserChoose"]
    user_choose = [c for c in visible if str(c.get("type") or "") == "flowUserChoose"]
    _balance_half_row_spans(regular)
    _balance_half_row_spans(user_choose)
    form_info_cols[:] = hidden + regular + user_choose


def _inject_self_select_form_fields(
    form_info_cols: list[dict],
    specs: list[SelfSelectFieldSpec],
    *,
    used_props: set[str],
) -> None:
    """为每个自选环节在表单信息末尾追加 flowUserChoose 字段（已有则跳过）。"""
    existing_props = {str(c.get("prop") or "") for c in form_info_cols}
    existing_labels = {str(c.get("label") or "") for c in form_info_cols}
    extras: list[dict] = []
    for spec in specs:
        if spec.field_label in existing_labels:
            continue
        if spec.field_prop in existing_props or spec.field_prop in used_props:
            continue
        extras.append(_build_flow_user_choose_column(label=spec.field_label, prop=spec.field_prop))
        used_props.add(spec.field_prop)
        existing_props.add(spec.field_prop)
        existing_labels.add(spec.field_label)
    if extras:
        form_info_cols.extend(extras)


def _ensure_field_contact_person(task_node: dict, *, prop: str, label: str) -> None:
    if not isinstance(task_node.get("properties"), dict):
        task_node["properties"] = {}
    field_contact = json.dumps(
        {"prop": prop, "label": label, "dataType": "flowUserChoose"},
        ensure_ascii=False,
    )
    task_node["properties"]["personList"] = [
        {
            "personType": "fieldContactPerson",
            "assignMember": None,
            "fieldContact": field_contact,
        }
    ]


def _link_self_select_task_persons(task_root: dict | None, specs: list[SelfSelectFieldSpec]) -> None:
    """自选审批/抄送环节办理人绑定到表单选人字段。"""
    if not task_root or not specs:
        return
    by_task = {s.task_name: s for s in specs}
    for node in iter_all_tasks(task_root):
        if node.get("type") not in {"USERTASK", "CCTASK"}:
            continue
        spec = by_task.get(str(node.get("taskName") or ""))
        if spec is None:
            continue
        _ensure_field_contact_person(node, prop=spec.field_prop, label=spec.field_label)


def _minimal_form_record(template_name: str) -> RawFormRecord:
    """新增流程：无 OA 原表单，仅保留系统字段 + 流程分支条件字段。"""
    return RawFormRecord(
        template_name=template_name,
        success=True,
        fields=[],
        raw={"minimal_form": True},
    )


def _build_form_info_columns(record: RawFormRecord, *, used_props: set[str]) -> list[dict]:
    """表单信息：部门 → 流程编号 → 发起人 → OA 业务字段。"""
    cols = _system_group_columns(used_props=used_props)
    cols.append(_initiator_column(used_props=used_props))
    for field in _collect_business_fields(record):
        label = (field.get("label") or "").strip()
        if label == "发起人":
            continue
        cols.append(_build_field_column(field, used_props=used_props))
    return cols


def _sync_approval_columns(form_model: dict, task_root: dict | None) -> None:
    """环节 taskKey 规范化后，同步重建审批意见分组字段 prop。"""
    approval_cols = _build_approval_columns(task_root)
    for group in form_model.get("group") or []:
        if group.get("label") == APPROVAL_GROUP_LABEL:
            group["column"] = approval_cols
            break


def finalize_workflow_model(model: dict, *, start_task_name: str | None = None) -> dict:
    """保存前统一补全环节 key、审批权限与 formModelJson。"""
    updated = copy.deepcopy(model)
    task_root = updated.get("wfSimpleTaskInfo")
    if isinstance(task_root, dict):
        updated["wfSimpleTaskInfo"] = _ensure_task_keys(task_root, start_task_name=start_task_name)
    form_model = updated.get("formModel")
    if isinstance(form_model, dict):
        _sync_approval_columns(form_model, updated.get("wfSimpleTaskInfo"))
        for group in form_model.get("group") or []:
            if group.get("label") == FORM_INFO_GROUP_LABEL:
                cols = group.get("column")
                if isinstance(cols, list):
                    _normalize_form_info_layout(cols)
                break
    _attach_task_buttons(updated)
    _attach_task_person_lists(updated)
    _attach_form_permissions(updated)
    from core.workflow.shenbi_client import sync_from_property_list_task_ids

    sync_from_property_list_task_ids(updated.get("wfSimpleTaskInfo"))
    updated = _normalize_form_permissions(updated)
    form_model = updated.get("formModel")
    if isinstance(form_model, dict):
        updated["formModelJson"] = json.dumps(form_model, ensure_ascii=False, separators=(",", ":"))
    from core.config.shenbi_environments import resolve_shenbi_tenant_id
    from core.workflow.shenbi_client import normalize_model_tenant_ids

    normalize_model_tenant_ids(updated, resolve_shenbi_tenant_id(get_settings()))
    return updated


def _default_base_button_default_set() -> list[dict]:
    """表单设计器默认按钮集（与神笔样例一致）。"""
    names = [
        ("保存草稿", "保存", True),
        ("提交表单", "保存并提交", True),
        ("", "审批详情", True),
        ("", "转办", False),
        ("", "委派", False),
        ("", "取消委派", False),
        ("", "加签", False),
        ("", "减签", False),
        ("", "抄送", False),
        ("", "终止", True),
        ("", "撤回", True),
        ("", "撤回重填", False),
        ("", "导出Excel", False),
        ("", "导出Pdf", False),
        ("", "催办", False),
        ("", "打印", False),
    ]
    return [
        {
            "reName": re_name,
            "display": display,
            "$cellEdit": True,
            "$index": idx,
            "name": name,
        }
        for idx, (re_name, name, display) in enumerate(names)
    ]


_TASK_BUTTON_DEFS: list[tuple[str, str, str, int]] = [
    ("同意", "extButton.agree", "true", 0),
    ("拒绝", "extButton.reject", "true", 1),
    ("退回", "extButton.return", "true", 2),
    ("保存", "button.save", "false", 3),
    ("转办", "button.turnTask", "false", 4),
    ("委派", "button.delegateTask", "false", 5),
    ("加签", "button.addTask", "false", 6),
    ("减签", "button.deleteTask", "false", 7),
    ("审批详情", "button.auditDetail", "false", 8),
    ("抄送", "button.ccProc", "false", 9),
    ("导出pdf", "button.pdfExport", "false", 10),
    ("导出Excel", "button.execlExport", "false", 11),
]


def _default_task_button_list(task_id: str) -> list[dict]:
    return [
        {
            "id": None,
            "taskId": task_id,
            "display": "true",
            "buttonName": name,
            "buttonValue": value,
            "needVerify": verify,
            "requiredField": "",
            "sort": sort,
            "creator": None,
            "createTime": None,
            "updator": None,
            "updateTime": None,
            "deleteFlag": None,
            "tenantId": None,
        }
        for name, value, verify, sort in _TASK_BUTTON_DEFS
    ]


# 神笔样例复杂流程默认办理人：管理员角色（P0 平台可跑；具体 roleCode 随工作环境切换）
_DEFAULT_ADMIN_ROLE_ID = "cc2497b91c7dd2f72668cf86447b03e6"


def _admin_role_info_json(*, settings: Settings) -> str:
    cfg = get_shenbi_config(settings)
    return json.dumps(
        {
            "id": cfg.admin_role_id,
            "name": cfg.admin_role_name,
            "relateStartOrgId": False,
            "roleCode": cfg.admin_role_code,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _default_role_person_entry(task_id: str, *, settings: Settings) -> dict:
    now = _now_str()
    return {
        "id": _new_id(),
        "taskId": task_id,
        "personType": "role",
        "assignMember": None,
        "fieldContact": None,
        "roleInfo": _admin_role_info_json(settings=settings),
        "creator": "wipadmin",
        "createTime": now,
        "updator": "wipadmin",
        "updateTime": now,
        "deleteFlag": 0,
        "tenantId": settings.shenbi_tenant_id,
    }


def _has_assignable_person_list(person_list: Any) -> bool:
    if not isinstance(person_list, list) or not person_list:
        return False
    first = person_list[0]
    if not isinstance(first, dict):
        return False
    person_type = str(first.get("personType") or "").strip()
    if person_type in {"assignMember", "fieldContactPerson"}:
        return True
    return bool(person_type)


def _attach_task_person_lists(model: dict) -> None:
    """USERTASK / CCTASK 补全 personList（样例为管理员角色；保留 assignMember）。"""
    settings = get_settings()

    def walk(node: dict | None) -> None:
        if not node:
            return
        task_type = node.get("type")
        if task_type in {"USERTASK", "CCTASK"}:
            task_id = str(node.get("id") or "")
            if not isinstance(node.get("properties"), dict):
                node["properties"] = {}
            props = node["properties"]
            existing = props.get("personList")
            if not _has_assignable_person_list(existing):
                task_name = str(node.get("taskName") or "")
                if task_type == "USERTASK" and "自选" in task_name:
                    props["personList"] = [{"personType": "assignMember"}]
                else:
                    props["personList"] = [_default_role_person_entry(task_id, settings=settings)]
        child = node.get("child")
        if isinstance(child, dict):
            walk(child)
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                walk(cond)

    walk(model.get("wfSimpleTaskInfo"))


def _attach_task_buttons(model: dict) -> None:
    """STARTTASK / USERTASK 补全 buttonList（model-save 转换所需）。"""

    def walk(node: dict | None) -> None:
        if not node:
            return
        task_type = node.get("type")
        if task_type in {"STARTTASK", "USERTASK"}:
            task_id = str(node.get("id") or "")
            if not isinstance(node.get("properties"), dict):
                node["properties"] = {}
            props = node["properties"]
            props.setdefault("buttonList", _default_task_button_list(task_id))
            props.setdefault("branchConditionList", None)
            props.setdefault("listenerList", None)
        child = node.get("child")
        if isinstance(child, dict):
            walk(child)
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                walk(cond)

    walk(model.get("wfSimpleTaskInfo"))


def _default_ext_buttons() -> list[dict]:
    return [
        {
            "other": '{"variableMap":{"branchVariable":"agree"}}',
            "onClick": "submit",
            "buttonStyleType": "success",
            "label": "审核意见",
            "prop": "agree",
            "name": "同意",
            "order": 23,
        },
        {
            "other": '{"variableMap":{"branchVariable":"reject"}}',
            "onClick": "submit",
            "buttonStyleType": "success",
            "label": "审核意见",
            "prop": "reject",
            "name": "拒绝",
            "order": 23,
        },
        {
            "other": '{"variableMap":{"branchVariable":"return"}}',
            "onClick": "submit",
            "buttonStyleType": "success",
            "label": "审核意见",
            "prop": "return",
            "name": "退回",
            "order": 23,
        },
    ]


def _form_field_index(form_model: dict) -> tuple[list[dict], list[dict]]:
    form_info: list[dict] = []
    approval: list[dict] = []
    for group in form_model.get("group") or []:
        label = group.get("label")
        cols = group.get("column") or []
        if label == FORM_INFO_GROUP_LABEL:
            form_info = cols
        elif label == APPROVAL_GROUP_LABEL:
            approval = cols
    return form_info, approval


def _all_form_columns(form_model: dict) -> list[dict]:
    cols: list[dict] = []
    for group in form_model.get("group") or []:
        cols.extend(group.get("column") or [])
    return cols


def _field_permission_entry(field: dict, operating: str, task_id: str | None) -> dict:
    return {
        "id": _new_id(),
        "taskId": task_id if task_id else None,
        "fieldName": clamp_platform_field_name(field.get("label") or field.get("prop") or ""),
        "fieldProp": field.get("prop"),
        "operating": operating,
        "isField": "true",
        "creator": "wipadmin",
        "createTime": _now_str(),
        "updator": "wipadmin",
        "updateTime": _now_str(),
        "deleteFlag": 0,
        "tenantId": None,
    }


def _zwjb_operating(task_type: str, task_key: str | None) -> str:
    if task_type == "STARTTASK":
        return "writable"
    return "readable"


def _attach_form_permissions(model: dict) -> None:
    """按规律写入 STARTTASK / USERTASK 的 fromPropertyList（与神笔样例结构一致）。"""
    form_model = model.get("formModel")
    task_root = model.get("wfSimpleTaskInfo")
    if not isinstance(form_model, dict) or not isinstance(task_root, dict):
        return

    form_info, approval = _form_field_index(form_model)
    visible_form = [c for c in form_info if c.get("display") is not False and c.get("prop")]

    def walk(node: dict | None) -> None:
        if not node:
            return
        task_type = node.get("type")
        task_key = str(node.get("taskKey") or "")
        task_id = node.get("id")
        if task_id is not None:
            task_id = str(task_id)

        if not isinstance(node.get("properties"), dict):
            node["properties"] = {}
        props = node["properties"]

        if task_type not in {"STARTTASK", "USERTASK"}:
            props["fromPropertyList"] = None
        else:
            entries: list[dict] = []

            if task_type == "STARTTASK":
                for col in visible_form:
                    prop = col.get("prop")
                    if prop == "zwjb":
                        entries.append(_field_permission_entry(col, "writable", task_id))
                    elif prop in SYSTEM_READONLY_PROPS:
                        entries.append(_field_permission_entry(col, "readable", task_id))
                    else:
                        entries.append(_field_permission_entry(col, "writable", task_id))
                for col in approval:
                    entries.append(_field_permission_entry(col, "readable", task_id))
            else:
                for col in visible_form:
                    prop = col.get("prop")
                    if prop == "zwjb":
                        entries.append(
                            _field_permission_entry(
                                col, _zwjb_operating(task_type, task_key), task_id
                            )
                        )
                    else:
                        entries.append(_field_permission_entry(col, "readable", task_id))
                for col in approval:
                    prop = col.get("prop")
                    if prop == task_key:
                        op = "writable"
                    else:
                        op = "readable"
                    entries.append(_field_permission_entry(col, op, task_id))
            props["fromPropertyList"] = entries

        child = node.get("child")
        if isinstance(child, dict):
            walk(child)
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                walk(cond)

    walk(task_root)


def _build_approval_columns(task_root: dict | None) -> list[dict]:
    cols: list[dict] = []
    seen: set[str] = set()
    for node in iter_all_tasks(task_root):
        if node.get("type") != "USERTASK":
            continue
        task_key = node.get("taskKey")
        task_name = node.get("taskName") or "审批"
        if not task_key or task_key in seen:
            continue
        seen.add(str(task_key))
        label = task_name if ("意见" in task_name or "审批" in task_name) else f"{task_name}意见"
        cols.append(
            {
                "shieldProcessRecord": True,
                "indb": True,
                "labelslot": True,
                "display": True,
                "dataType": "text",
                "prop": task_key,
                "rules": [],
                "label": label,
                "type": "textarea",
                "span": 24,
            }
        )
    return cols


def _build_form_model_shell(
    *,
    form_info_cols: list[dict],
    approval_cols: list[dict],
    form_id: str | None,
    workflow_name: str,
    table_name: str | None,
) -> dict:
    ts = str(int(datetime.now().timestamp() * 1000))
    return {
        "apiParam": {"apiParam": []},
        "column": [],
        "labelPosition": "right",
        "labelSuffix": "：",
        "labelWidth": 150,
        "gutter": 0,
        "menuBtn": False,
        "submitBtn": False,
        "emptyBtn": False,
        "formId": form_id,
        "baseButtonDefaultSet": _default_base_button_default_set(),
        "tableComment": workflow_name,
        "tableName": table_name or None,
        "noLogin": True,
        "extButtonList": _default_ext_buttons(),
        "btnLay": "foot",
        "group": [
            {
                "arrow": False,
                "prop": f"{ts}_form",
                "display": True,
                "label": FORM_INFO_GROUP_LABEL,
                "collapse": True,
                "column": form_info_cols,
            },
            {
                "arrow": False,
                "prop": f"{ts}_approval",
                "display": True,
                "label": APPROVAL_GROUP_LABEL,
                "collapse": True,
                "column": approval_cols,
            },
        ],
    }


def _build_wf_simple_proc(
    *,
    workflow_name: str,
    form_id: str | None,
    proc_id: str | None,
    proc_key: str | None,
    settings: Settings,
    app_id: str | None,
    remark: str,
) -> dict:
    now = _now_str()
    return {
        "id": proc_id,
        "procName": workflow_name,
        "procKey": proc_key or None,
        "fromId": form_id,
        "rejectToNotify": "false",
        "rejectMsgTemp": "你发起的${title}事项审批未通过，请尽快查看！",
        "returnToNotify": "false",
        "returnMsgTemp": "你发起的${title}事项被退回，请尽快查看！",
        "completeToNotify": "false",
        "completeMsgTemp": "你发起的${title}事项审批通过，请尽快查看！",
        "creator": "wipadmin",
        "createTime": now,
        "updator": "wipadmin",
        "updateTime": now,
        "remark": remark,
        "appId": app_id or settings.shenbi_app_id,
        "complexProcId": None,
        "deleteFlag": 0,
        "tenantId": settings.shenbi_tenant_id,
    }


def _allocate_task_key(name: str, used: set[str], *, index: int) -> str:
    if index == 0:
        base = "todo"
    else:
        base = _task_key_from_name(name, index=index)
    if not is_valid_ascii_prop(base):
        base = task_key_from_name(name, index=index)
    key = base
    if key in used:
        suffix = 2
        while f"{base}{suffix}" in used:
            suffix += 1
        key = f"{base}{suffix}" if index == 0 else f"{base}_{suffix}"
    used.add(key)
    return key


def _build_linear_task_tree_from_steps(
    steps: list[SemanticStep],
    *,
    proc_id: str,
    settings: Settings,
) -> dict:
    start_steps = [s for s in steps if s.kind == "start"]
    approval_steps = [s for s in steps if s.kind == "approval"]
    cc_steps = [s for s in steps if s.kind == "cc"]
    start_name = start_steps[0].task_name if start_steps else "提交人"

    used_keys: set[str] = set()
    root = _make_task_from_template(
        None,
        task_name=start_name,
        task_key="sqtb",
        task_type="STARTTASK",
        proc_id=proc_id,
        pid=None,
        sort=0,
        settings=settings,
    )
    used_keys.add("sqtb")
    prev = root

    for idx, step in enumerate(approval_steps):
        key = _allocate_task_key(step.task_name, used_keys, index=idx)
        node = _make_task_from_template(
            None,
            task_name=step.task_name,
            task_key=key,
            task_type="USERTASK",
            proc_id=proc_id,
            pid=prev["id"],
            sort=idx,
            settings=settings,
        )
        if step.assign_by_initiator:
            _ensure_assign_member(node)
        prev["child"] = node
        prev = node

    if cc_steps:
        cc_key = "cc" if "cc" not in used_keys else "cc2"
        used_keys.add(cc_key)
        cc_node = _make_task_from_template(
            None,
            task_name=cc_steps[0].task_name,
            task_key=cc_key,
            task_type="CCTASK",
            proc_id=proc_id,
            pid=prev["id"],
            sort=0,
            settings=settings,
        )
        prev["child"] = cc_node

    return root


def _is_skippable_business_label(label: str) -> bool:
    text = (label or "").strip()
    if not text or text in _SKIP_BUSINESS_LABELS:
        return True
    if text in LABEL_PROP_MAP:
        return True
    if _JUNK_FIELD_LABEL_RE.match(text):
        return True
    return False


def label_to_prop(label: str, used: set[str]) -> str:
    """
    字段 label → 神笔 prop。

    平台要求 prop 为 ASCII 标识符且以字母开头（如 userName、xm），
    不可使用中文或纯数字。
    """
    text = (label or "").strip()
    if text in LABEL_PROP_MAP:
        prop = LABEL_PROP_MAP[text]
    elif text in BUSINESS_LABEL_PROP_MAP:
        prop = BUSINESS_LABEL_PROP_MAP[text]
    else:
        ascii_part = re.sub(r"[^a-zA-Z0-9]", "", text)
        if ascii_part and ascii_part[0].isalpha():
            prop = ascii_part[:24].lower()
        else:
            prop = "f" + hashlib.md5(text.encode("utf-8")).hexdigest()[:8]

    if not _VALID_PROP_RE.match(prop):
        prop = "f" + hashlib.md5(text.encode("utf-8")).hexdigest()[:8]

    base = prop
    idx = 2
    while prop in used:
        prop = f"{base}{idx}"
        idx += 1
    used.add(prop)
    return prop


def _map_field_type(input_type: str, tag: str) -> str:
    t = (input_type or tag or "text").lower()
    if t in {"date", "datetime"}:
        return "date"
    if t in {"select", "radio"}:
        return "select"
    if t == "textarea":
        return "textarea"
    return "input"


def _build_field_column(field: dict, *, used_props: set[str]) -> dict:
    label = field.get("label") or ""
    prop = label_to_prop(label, used_props)
    field_type = _map_field_type(field.get("input_type", ""), field.get("tag", ""))
    col: dict[str, Any] = {
        "indb": True,
        "maxlength": 200,
        "display": True,
        "dataType": "varchar",
        "prop": prop,
        "rules": [],
        "label": label,
        "type": field_type,
        # 与神笔样例一致：textarea 独占一行（span=24），其余半行（span=12）
        "span": 24 if field_type == "textarea" else 12,
    }
    if col["type"] == "date":
        col["format"] = "yyyy-MM-dd"
        col["valueFormat"] = "yyyy-MM-dd"
    return col


def _system_group_columns(*, used_props: set[str]) -> list[dict]:
    used_props.update({"id", "processInstanceId", "taskId", "creator", "createTime", "lastUpdator", "lastUpdateTime", "orgId", "processCode"})
    return [
        {"indb": True, "maxlength": 32, "display": False, "prop": "id", "label": "主表关键字", "type": "input", "span": 12},
        {"indb": True, "maxlength": 64, "display": False, "prop": "processInstanceId", "label": "流程实例ID", "type": "input", "span": 12},
        {"indb": True, "maxlength": 64, "display": False, "prop": "taskId", "label": "环节Id", "type": "input", "span": 12},
        {"indb": True, "maxlength": 32, "display": False, "prop": "creator", "label": "创建人ID", "type": "input", "span": 12},
        {"indb": True, "maxlength": 20, "display": False, "prop": "createTime", "label": "创建时间", "type": "input", "span": 12},
        {"indb": True, "maxlength": 32, "display": False, "prop": "lastUpdator", "label": "最后修改人", "type": "input", "span": 12},
        {"indb": True, "maxlength": 20, "display": False, "prop": "lastUpdateTime", "label": "最后修改时间", "type": "input", "span": 24},
        {
            "cascaderItem": [],
            "dicData": [],
            "display": True,
            "dataType": "varchar",
            "rules": [],
            "label": "部门",
            "type": "select",
            "dynamicHide": [],
            "props": {},
            "isDepartSelect": True,
            "filter": {"dynamic": []},
            "indb": True,
            "prop": "orgId",
            "localDic": [],
            "dicFlag": True,
            "disabled": True,
            "dicOption": "1",
            "span": 12,
        },
        {
            "indb": True,
            "maxlength": 60,
            "display": True,
            "prop": "processCode",
            "dataType": "varchar",
            "disabled": False,
            "rules": [],
            "label": "流程编号",
            "type": "input",
            "span": 12,
        },
    ]


def _collect_business_fields(record: RawFormRecord) -> list[dict]:
    seen: set[str] = set()
    fields: list[dict] = []
    for item in record.fields:
        label = (item.label or "").strip()
        if _is_skippable_business_label(label) or label in seen:
            continue
        seen.add(label)
        fields.append(item.model_dump())
    raw = record.raw or {}
    for label in raw.get("domLabels") or raw.get("inputLabels") or []:
        label = (label or "").strip()
        if _is_skippable_business_label(label) or label in seen:
            continue
        seen.add(label)
        fields.append({"label": label, "input_type": "text", "tag": "input"})
    return fields


def merge_raw_form_records(records: list[RawFormRecord], *, template_name: str) -> RawFormRecord:
    """合并多个 OA 表单字段（按 label 去重取并集）。"""
    if not records:
        raise ValueError("无可合并的表单")
    if len(records) == 1:
        return records[0]

    merged_fields: list = []
    seen: set[str] = set()
    dom_labels: list[str] = []
    for record in records:
        for item in record.fields:
            label = (item.label or "").strip()
            if _is_skippable_business_label(label) or not label or label in seen:
                continue
            seen.add(label)
            merged_fields.append(item)
        raw = record.raw or {}
        for label in raw.get("domLabels") or raw.get("inputLabels") or []:
            label = (label or "").strip()
            if _is_skippable_business_label(label) or not label or label in seen:
                continue
            seen.add(label)
            dom_labels.append(label)

    return RawFormRecord(
        template_name=template_name,
        template_id=records[0].template_id,
        template_url=records[0].template_url,
        oa_category=records[0].oa_category,
        success=all(r.success for r in records),
        fields=merged_fields,
        raw={"domLabels": dom_labels, "merged_from": [r.template_name for r in records]},
        extracted_at=records[0].extracted_at,
    )


def _sync_task_tree_proc_ids(task: dict | None, *, proc_id: str, pid: str | None = None) -> dict | None:
    """保留设计器环节 id，同步 procId/pid（含 conditions 分支）。"""
    if not task:
        return None
    node = copy.deepcopy(task)
    node["id"] = node.get("id") or _new_id()
    node["procId"] = proc_id
    node["pid"] = pid
    node["deleteFlag"] = 0
    node["creator"] = node.get("creator") or "wipadmin"
    node["updator"] = node.get("updator") or "wipadmin"
    node["createTime"] = node.get("createTime") or _now_str()
    node["updateTime"] = _now_str()

    props = node.get("properties")
    if isinstance(props, dict):
        for fp in props.get("fromPropertyList") or []:
            fp["taskId"] = node["id"]
        for btn in props.get("buttonList") or []:
            btn["taskId"] = node["id"]
        for bc in props.get("branchConditionList") or []:
            bc["taskId"] = node["id"]

    child = node.get("child")
    if child:
        node["child"] = _sync_task_tree_proc_ids(child, proc_id=proc_id, pid=node["id"])
    synced_conditions: list[dict] = []
    for cond in node.get("conditions") or []:
        if isinstance(cond, dict):
            synced = _sync_task_tree_proc_ids(cond, proc_id=proc_id, pid=node["id"])
            if synced:
                synced_conditions.append(synced)
    if synced_conditions:
        node["conditions"] = synced_conditions
    return node


def _regenerate_task_tree(task: dict | None, *, proc_id: str, pid: str | None = None) -> dict | None:
    if not task:
        return None
    node = copy.deepcopy(task)
    task_id = _new_id()
    node["id"] = task_id
    node["procId"] = proc_id
    node["pid"] = pid
    node["creator"] = node.get("creator") or "wipadmin"
    node["updator"] = node.get("updator") or "wipadmin"
    node["createTime"] = _now_str()
    node["updateTime"] = _now_str()
    node["deleteFlag"] = 0

    props = node.get("properties")
    if isinstance(props, dict):
        for fp in props.get("fromPropertyList") or []:
            fp["id"] = _new_id()
            fp["taskId"] = task_id
        for btn in props.get("buttonList") or []:
            btn["id"] = _new_id()
            btn["taskId"] = task_id
            btn["createTime"] = _now_str()
            btn["updateTime"] = _now_str()

    child = node.get("child")
    if child:
        node["child"] = _regenerate_task_tree(child, proc_id=proc_id, pid=task_id)
    regen_conditions: list[dict] = []
    for cond in node.get("conditions") or []:
        if isinstance(cond, dict):
            regen = _regenerate_task_tree(cond, proc_id=proc_id, pid=task_id)
            if regen:
                regen_conditions.append(regen)
    if regen_conditions:
        node["conditions"] = regen_conditions
    return node


def _task_key_from_name(name: str, *, index: int) -> str:
    return task_key_from_name(name, index=index)


def _extract_task_template(template_root: dict | None, task_type: str) -> dict | None:
    node = template_root
    while node:
        if node.get("type") == task_type:
            return node
        node = node.get("child")
    return None


def _make_task_from_template(
    template: dict | None,
    *,
    task_name: str,
    task_key: str,
    task_type: str,
    proc_id: str,
    pid: str | None,
    sort: int,
    settings: Settings,
) -> dict:
    base = copy.deepcopy(template) if template else {}
    task_id = _new_id()
    now = _now_str()
    node = {
        "id": task_id,
        "procId": proc_id,
        "pid": pid,
        "taskName": task_name[:60],
        "taskKey": task_key,
        "type": task_type,
        "isSign": base.get("isSign", "false"),
        "signFormId": base.get("signFormId"),
        "sendNotify": base.get("sendNotify", "false"),
        "notifyMsgTemp": "${startUserName}发起的${procName}事项，流转到${currTaskName}环节，请尽快办理！",
        "jumpIfRepeat": base.get("jumpIfRepeat"),
        "mergeAdjacent": base.get("mergeAdjacent"),
        "jumpIfMyApplication": base.get("jumpIfMyApplication"),
        "sort": sort,
        "creator": "wipadmin",
        "createTime": now,
        "updator": "wipadmin",
        "updateTime": now,
        "deleteFlag": 0,
        "tenantId": settings.shenbi_tenant_id,
        "child": None,
        "conditions": None,
        "properties": None,
    }
    props = base.get("properties")
    if isinstance(props, dict):
        node["properties"] = copy.deepcopy(props)
        for fp in node["properties"].get("fromPropertyList") or []:
            fp["id"] = _new_id()
            fp["taskId"] = task_id
        for btn in node["properties"].get("buttonList") or []:
            btn["id"] = _new_id()
            btn["taskId"] = task_id
            btn["createTime"] = now
            btn["updateTime"] = now
    return node


def _ensure_assign_member(task_node: dict) -> None:
    if not isinstance(task_node.get("properties"), dict):
        task_node["properties"] = {}
    task_node["properties"]["personList"] = [{"personType": "assignMember"}]


def _linearize_task_chain(root: dict | None) -> list[dict]:
    chain: list[dict] = []
    node = root
    while node:
        chain.append(node)
        node = node.get("child")
    return chain


def _ensure_task_keys(root: dict | None, *, start_task_name: str | None = None) -> dict | None:
    """补全环节 taskKey / taskName，model-save 校验二者均不能为空。"""
    if not root:
        return None

    used_keys: set[str] = set()
    usertask_idx = 0

    def visit(node: dict) -> None:
        nonlocal usertask_idx
        task_type = node.get("type")
        if task_type == "ROUTE":
            node["taskName"] = None
            node["taskKey"] = None
        elif task_type == "STARTTASK":
            node["taskName"] = start_task_name or node.get("taskName") or "申请填报"
            node.setdefault("taskKey", "sqtb")
        elif task_type == "USERTASK":
            node.setdefault("taskName", "办理")
            if not node.get("taskKey"):
                node["taskKey"] = "todo" if usertask_idx == 0 else _task_key_from_name(
                    node["taskName"], index=usertask_idx
                )
            usertask_idx += 1
        elif task_type == "CCTASK":
            node.setdefault("taskName", "抄送")
            node.setdefault("taskKey", "cc")
        elif task_type == "BRANCHTASK":
            node.setdefault("taskName", node.get("taskName") or "分支")
            node.setdefault("taskKey", node.get("taskKey") or f"branch_{usertask_idx}")

        key = str(node.get("taskKey") or "").strip()
        if task_type != "ROUTE":
            if not key or not is_valid_ascii_prop(key):
                key = _task_key_from_name(
                    node.get("taskName") or task_type or "task",
                    index=max(usertask_idx, 1),
                )
                node["taskKey"] = key
            if key in used_keys:
                suffix = 2
                base = key
                while f"{base}_{suffix}" in used_keys:
                    suffix += 1
                key = f"{base}_{suffix}"
                node["taskKey"] = key
            used_keys.add(key)

        child = node.get("child")
        if isinstance(child, dict):
            visit(child)
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                visit(cond)

    visit(root)
    return root


def _build_task_tree(
    workflow_name: str,
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
) -> tuple[dict | None, str]:
    """P2：办事指南 DAG → 环节树（招聘并行 / 通用 fan-out / 线性，由拓扑自动选型）。"""
    return compile_task_tree_from_guide(
        workflow_name,
        nodes,
        proc_id=proc_id,
        settings=settings,
    )


def _build_task_tree_from_guide_nodes(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
    workflow_name: str = "",
) -> tuple[dict | None, str]:
    """办事指南节点 → 程序化环节树（线性或复杂）。"""
    return _build_task_tree(
        workflow_name,
        nodes,
        proc_id=proc_id,
        settings=settings,
    )


def parsed_flow_matches_catalog(workflow_name: str, flow: WorkflowFlowResult) -> bool:
    """parse 结果标题须与 catalog 流程名足够接近，避免误匹配其他板块流程。"""
    hints = get_catalog_hints(workflow_name)
    candidates = [str(flow.title or ""), str(flow.image.title_hint or "")]
    for hint in hints:
        hint_norm = normalize_workflow_name(hint)
        if not hint_norm:
            continue
        for cand in candidates:
            cand_norm = normalize_workflow_name(cand)
            if hint_norm == cand_norm:
                return True
            if string_similarity(hint, cand) >= 0.92:
                return True
    return False


def find_parsed_flow(workflow_name: str, *, settings: Settings | None = None) -> WorkflowFlowResult | None:
    settings = settings or get_settings()
    result = load_result(settings)
    if not result:
        return None
    hints = get_catalog_hints(workflow_name)
    best: WorkflowFlowResult | None = None
    best_score = 0.0
    best_nodes = -1
    for item in result.sector_results:
        for flow in item.flows:
            if flow.skipped or not flow.success or not flow.nodes:
                continue
            for hint in hints:
                hint_norm = normalize_workflow_name(hint)
                for field, candidate in (
                    ("hint", flow.image.title_hint),
                    ("title", flow.title),
                ):
                    score = string_similarity(hint, candidate)
                    cand_norm = normalize_workflow_name(candidate)
                    if hint_norm and hint_norm == cand_norm:
                        score = 1.0
                    if field == "hint":
                        score += 0.001
                    if score > best_score or (score == best_score and len(flow.nodes) > best_nodes):
                        best_score = score
                        best_nodes = len(flow.nodes)
                        best = flow
    if best_score >= 0.82 and best is not None and parsed_flow_matches_catalog(workflow_name, best):
        return best
    return None


def _customize_model(
    *,
    workflow_name: str,
    record: RawFormRecord,
    settings: Settings,
    shared: dict,
    parsed_flow: WorkflowFlowResult,
    registry: dict | None = None,
    app_display_name: str | None = None,
    form_display_name: str | None = None,
) -> dict:
    display_name = app_display_name or display_app_name(workflow_name)
    form_name = form_display_name or display_form_name(workflow_name)
    reg = _registry_ids(registry)
    # 统一写入路径：form/proc 由 save 层 create；生成 JSON 时仅复用 appId
    form_id = None
    proc_id = None
    proc_key = None
    table_name = None
    app_id = reg.get("app_id") or None

    if not parsed_flow.nodes:
        raise ValueError(
            f"未找到「{workflow_name}」的办事指南流程解析结果，请先在「流程解析」页完成解析并保存"
        )
    semantic_steps = guide_nodes_to_semantic_steps(parsed_flow.nodes)
    start_steps = [s for s in semantic_steps if s.kind == "start"]

    task_root, task_tree_source = _build_task_tree_from_guide_nodes(
        parsed_flow.nodes,
        proc_id=proc_id,
        settings=settings,
        workflow_name=workflow_name,
    )
    if not task_root:
        raise ValueError(f"无法从办事指南节点生成流程环节：{workflow_name}")
    shared["task_tree_source"] = task_tree_source
    if is_recruitment_task_tree_source(task_tree_source):
        bindings = summarize_recruitment_branch_plans(parsed_flow.nodes)
        shared["dept_branch_bindings"] = bindings
    if is_expense_task_tree_source(task_tree_source):
        shared["expense_branch_bindings"] = summarize_expense_branch_bindings(parsed_flow.nodes)

    used_props: set[str] = set()
    form_info_cols = _build_form_info_columns(record, used_props=used_props)
    existing_labels = {str(c.get("label") or "") for c in form_info_cols}
    if is_recruitment_task_tree_source(task_tree_source):
        branch_props = {c["prop"] for c in recruitment_branch_form_fields()}
        insert_at = len(form_info_cols)
        for idx, col in enumerate(form_info_cols):
            if col.get("prop") == "fqr":
                insert_at = idx + 1
                break
        extras = []
        for col in recruitment_branch_form_fields():
            if col["prop"] not in used_props:
                used_props.add(col["prop"])
                extras.append(col)
        form_info_cols[insert_at:insert_at] = extras
        used_props.update(branch_props)
    elif is_expense_task_tree_source(task_tree_source):
        insert_at = len(form_info_cols)
        for idx, col in enumerate(form_info_cols):
            if col.get("prop") == "fqr":
                insert_at = idx + 1
                break
        extras = []
        branch_cols = (
            foreign_trade_expense_branch_form_fields(existing_labels=existing_labels)
            if task_tree_source == "guide_dag_compiler:foreign_trade_expense"
            else expense_branch_form_fields(existing_labels=existing_labels)
        )
        for col in branch_cols:
            if col["prop"] not in used_props:
                used_props.add(col["prop"])
                extras.append(col)
        if task_tree_source == "guide_dag_compiler:foreign_trade_expense":
            ensure_fybm_select_field(form_info_cols, used_props=used_props)
        form_info_cols[insert_at:insert_at] = extras
    elif JEFD_FIELD in branch_field_props_in_tree(task_root):
        insert_at = len(form_info_cols)
        for idx, col in enumerate(form_info_cols):
            if col.get("prop") == "fqr":
                insert_at = idx + 1
                break
        extras = []
        for col in amount_branch_form_fields(existing_labels=existing_labels):
            if col["prop"] not in used_props:
                used_props.add(col["prop"])
                extras.append(col)
        form_info_cols[insert_at:insert_at] = extras
    elif FYLX_FIELD in branch_field_props_in_tree(task_root):
        insert_at = len(form_info_cols)
        for idx, col in enumerate(form_info_cols):
            if col.get("prop") == "fqr":
                insert_at = idx + 1
                break
        extras = []
        for col in expense_type_branch_form_fields(existing_labels=existing_labels):
            if col["prop"] not in used_props:
                used_props.add(col["prop"])
                extras.append(col)
        form_info_cols[insert_at:insert_at] = extras
    elif ZGS_FIELD in branch_field_props_in_tree(task_root) or SLYZ_FIELD in branch_field_props_in_tree(
        task_root
    ):
        insert_at = len(form_info_cols)
        for idx, col in enumerate(form_info_cols):
            if col.get("prop") == "fqr":
                insert_at = idx + 1
                break
        extras = []
        for col in subsidiary_branch_form_fields(existing_labels=existing_labels):
            if col["prop"] not in used_props:
                used_props.add(col["prop"])
                extras.append(col)
        form_info_cols[insert_at:insert_at] = extras
    raw_self_select = collect_self_select_field_specs(parsed_flow.nodes)
    raw_self_select += collect_self_select_cc_specs(parsed_flow.nodes)
    seen_task_names: set[str] = set()
    merged_self_select: list[SelfSelectFieldSpec] = []
    for spec in raw_self_select:
        if spec.task_name in seen_task_names:
            continue
        seen_task_names.add(spec.task_name)
        merged_self_select.append(spec)
    self_select_specs = _resolve_self_select_specs(
        merged_self_select,
        form_info_cols,
        used_props=used_props,
    )
    _inject_self_select_form_fields(form_info_cols, self_select_specs, used_props=used_props)
    _normalize_form_info_layout(form_info_cols)
    task_root = _ensure_task_keys(task_root, start_task_name=(
        "申请填报"
        if is_recruitment_task_tree_source(task_tree_source)
        or is_expense_task_tree_source(task_tree_source)
        else (start_steps[0].task_name if start_steps else None)
    ))
    approval_cols = _build_approval_columns(task_root)

    model = {
        "id": None,
        "formModelJson": None,
        "formModel": _build_form_model_shell(
            form_info_cols=form_info_cols,
            approval_cols=approval_cols,
            form_id=form_id,
            workflow_name=form_name,
            table_name=table_name,
        ),
        "wfSimpleProc": _build_wf_simple_proc(
            workflow_name=display_name,
            form_id=form_id,
            proc_id=proc_id,
            proc_key=proc_key,
            settings=settings,
            app_id=app_id,
            remark=f"由野马智能交付工作台生成 · OA 模板 {record.template_name}",
        ),
        "wfSimpleTaskInfo": task_root,
    }
    _link_self_select_task_persons(task_root, self_select_specs)
    shared["semantic_flow_chain"] = semantic_steps_summary(semantic_steps)

    return finalize_workflow_model(
        model,
        start_task_name=(
            "申请填报"
            if is_recruitment_task_tree_source(task_tree_source)
            or is_expense_task_tree_source(task_tree_source)
            else (start_steps[0].task_name if start_steps else None)
        ),
    )


def build_workflow_payloads(
    workflow_name: str,
    *,
    settings: Settings | None = None,
    registry: dict | None = None,
) -> dict[str, Any]:
    """程序化生成完整 model-save 请求体（不读取 data 样例 JSON）。"""
    settings = settings or get_settings()
    meta = get_catalog_meta(workflow_name, settings=settings)

    from core.workflow.shenbi_client import load_workflow_registry, registry_app_verified

    if registry is None:
        registry = load_workflow_registry(workflow_name, settings=settings)
    if registry is not None and not registry_app_verified(registry):
        registry = None

    record = load_form(str(meta["form_slug"]), settings)
    if not record or not record.success:
        raise FileNotFoundError(f"未找到已成功提取的 OA 表单：{meta['form_slug']}")

    parsed_flow = resolve_parsed_flow(workflow_name, settings=settings)
    business_fields = _collect_business_fields(record)
    if not business_fields:
        raise ValueError(f"OA 表单 {meta['form_slug']} 无可用业务字段，无法生成神笔表单")

    shared: dict[str, str] = {}
    model = _customize_model(
        workflow_name=workflow_name,
        record=record,
        settings=settings,
        shared=shared,
        parsed_flow=parsed_flow,
        registry=registry,
    )

    return {
        "workflow_name": workflow_name,
        "generated_at": _now_str(),
        "source_form": record.template_name,
        "field_count": len(business_fields),
        "parsed_flow_title": (parsed_flow.title or parsed_flow.image.title_hint) if parsed_flow else "",
        "parsed_node_count": len(parsed_flow.nodes) if parsed_flow else 0,
        "task_tree_source": shared.get("task_tree_source") or "",
        "dept_branch_bindings": shared.get("dept_branch_bindings") or [],
        "expense_branch_bindings": shared.get("expense_branch_bindings") or [],
        "parse_warnings": shared.get("parse_warnings") or [],
        "semantic_flow_chain": shared.get("semantic_flow_chain") or [],
        "model": model,
        "is_update": bool(registry),
    }


def save_generated_payloads(payloads: dict[str, Any], *, settings: Settings | None = None) -> Path:
    out_dir = (settings or get_settings()).output_dir / "workflows" / "shenbi"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = workflow_registry_slug(payloads.get("workflow_name") or "workflow")
    path = out_dir / f"{slug}_payloads.json"
    path.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_template_workflow_payloads(
    target,
    *,
    settings: Settings | None = None,
    parsed_flow: WorkflowFlowResult | None = None,
) -> dict[str, Any]:
    """基于模板表匹配目标生成 model-save JSON（AI_ 应用名，不复用旧 registry）。"""
    from core.workflow.result_store import resolve_parsed_flow
    from core.workflow.shenbi_client import load_workflow_registry, registry_app_verified
    from core.workflow.template_matcher import (
        ai_app_display_name,
        find_parsed_flow_by_guide,
        load_template_form_record,
        template_registry_name,
    )

    settings = settings or get_settings()
    if not target.is_new_form and not target.form_slug and not target.form_slugs:
        raise ValueError(target.skip_reason or f"流程「{target.guide_title}」缺少 OA 表单")

    if target.is_new_form or not target.form_slugs:
        record = _minimal_form_record(target.guide_title)
    elif target.form_slugs:
        records = []
        for slug in target.form_slugs:
            rec = load_template_form_record(slug, settings=settings)
            if rec:
                records.append(rec)
        if not records:
            raise FileNotFoundError(f"未找到已成功提取的 OA 表单：{target.form_slugs}")
        record = merge_raw_form_records(records, template_name=target.form_slug or target.guide_title)
    else:
        record = load_template_form_record(target.form_slug, settings=settings)
        if not record:
            raise FileNotFoundError(f"未找到已成功提取的 OA 表单：{target.form_slug}")

    flow = parsed_flow or find_parsed_flow_by_guide(
        target.guide_title,
        sector_index=target.guide_sector_index,
        settings=settings,
    )
    if not flow or not flow.nodes:
        flow = resolve_parsed_flow(target.workflow_key, settings=settings)

    registry_key = template_registry_name(target.target_key)
    registry = load_workflow_registry(registry_key, settings=settings)
    if registry is not None and not registry_app_verified(registry):
        registry = None

    business_fields = _collect_business_fields(record)
    if not target.is_new_form and not business_fields:
        raise ValueError(f"OA 表单 {target.form_slug} 无可用业务字段，无法生成神笔表单")

    shared: dict[str, str] = {}
    model = _customize_model(
        workflow_name=target.workflow_key,
        record=record,
        settings=settings,
        shared=shared,
        parsed_flow=flow,
        registry=registry,
        app_display_name=target.app_display_name or ai_app_display_name(target.guide_title),
        form_display_name=display_form_name(target.guide_title),
    )

    return {
        "workflow_name": registry_key,
        "template_target_key": target.target_key,
        "generated_at": _now_str(),
        "source_form": record.template_name,
        "source_forms": list(target.form_slugs) if target.form_slugs else ([record.template_name] if not target.is_new_form else []),
        "field_count": len(business_fields),
        "is_new_form": target.is_new_form,
        "parsed_flow_title": (flow.title or flow.image.title_hint) if flow else "",
        "parsed_node_count": len(flow.nodes) if flow else 0,
        "task_tree_source": shared.get("task_tree_source") or "",
        "dept_branch_bindings": shared.get("dept_branch_bindings") or [],
        "expense_branch_bindings": shared.get("expense_branch_bindings") or [],
        "parse_warnings": shared.get("parse_warnings") or [],
        "semantic_flow_chain": shared.get("semantic_flow_chain") or [],
        "model": model,
        "is_update": bool(registry),
        "template_mode": True,
        "app_display_name": target.app_display_name,
    }
