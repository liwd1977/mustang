"""复杂流程（ROUTE / BRANCHTASK）程序化组装。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core.config.settings import Settings
from core.workflow.flow_semantics import strip_person_name

# 招聘需求：分支判断字段
ZPBM_FIELD = "zpbm"
ZWJB_FIELD = "zwjb"

ZPBM_OPTIONS: list[dict[str, str]] = [
    {"label": "集团党委办公室", "value": "0"},
    {"label": "总裁办", "value": "1"},
    {"label": "董办", "value": "2"},
    {"label": "集团财务部", "value": "3"},
    {"label": "集团采购部", "value": "4"},
    {"label": "集团纪检部", "value": "5"},
    {"label": "集团审计部", "value": "6"},
    {"label": "集团工会", "value": "7"},
]

ZWJB_OPTIONS: list[dict[str, str]] = [
    {"label": "员工级", "value": "0"},
    {"label": "主管级", "value": "1"},
    {"label": "经理级", "value": "2"},
    {"label": "总监/分管级", "value": "3"},
]

ZWJB_MANAGER_UP = [ZWJB_OPTIONS[2], ZWJB_OPTIONS[3]]
ZWJB_MANAGER_DOWN = [ZWJB_OPTIONS[0], ZWJB_OPTIONS[1]]

# 「是审计部」→ 集团审计部(value=6)；「不是审计部」→ 其余部门（与流程图语义一致）
ZPBM_IS_AUDIT = [{"label": "集团审计部", "value": "6"}]
ZPBM_NOT_AUDIT = [o for o in ZPBM_OPTIONS if o["value"] != "6"]

# 集团费用报销单：分支判断字段
FYSY_FIELD = "fysy"
SFZCB_FIELD = "sfzcb"
JEFD_FIELD = "jefd"
SFSJB_FIELD = "sfsjb"
CNLX_FIELD = "cnlx"
FYLX_FIELD = "fylx"
ZGS_FIELD = "zgs"
SLYZ_FIELD = "slyz"
FXWZ_FIELD = "fxwz"

FYSY_PROCUREMENT = {"label": "采购费用", "value": "0"}
FYSY_NON_PROCUREMENT = {"label": "非采购费用", "value": "1"}
SFZCB_YES = {"label": "是", "value": "1"}
SFZCB_NO = {"label": "否", "value": "0"}
JEFD_LT_10K = {"label": "小于1万", "value": "0"}
JEFD_GTE_10K = {"label": "大于等于1万", "value": "1"}
SFSJB_YES = {"label": "是", "value": "1"}
SFSJB_NO = {"label": "否", "value": "0"}
CNLX_OPTIONS: list[dict[str, str]] = [
    {"label": "银行出纳", "value": "0"},
    {"label": "外勤出纳", "value": "1"},
]
FYLX_DAILY = {"label": "日常支付", "value": "0"}
FYLX_MAJOR = {"label": "重大事项", "value": "1"}

# 采购借款：子公司 / 丝路驿站（含连线标签「其他」「龙馆」）
ZGS_BRANCH_OPTIONS: list[dict[str, str]] = [
    {"label": "其他", "value": "0"},
    {"label": "马业部", "value": "1"},
    {"label": "丝路驿站", "value": "2"},
]
SLYZ_BRANCH_OPTIONS: list[dict[str, str]] = [
    {"label": "龙馆", "value": "0"},
    {"label": "其他", "value": "1"},
    {"label": "料场/马背/餐饮", "value": "2"},
]

# 采购借款：部门名称 5 路 + 部门下丝路驿站 2 路
BMMC_FIELD = "bmmc"
BMMC_BRANCH_OPTIONS: list[dict[str, str]] = [
    {"label": "物业公司", "value": "0"},
    {"label": "其他", "value": "1"},
    {"label": "工坊文化创意", "value": "2"},
    {"label": "丝路驿站", "value": "3"},
    {"label": "餐饮管理公司", "value": "4"},
]
BMMC_SLYZ_BRANCH_OPTIONS: list[dict[str, str]] = [
    {"label": "其他", "value": "0"},
    {"label": "料场/马背/餐饮", "value": "1"},
]

# 外贸板块费用报销
SFSCGB_FIELD = "sfscgb"
SFSCGB_YES = {"label": "是", "value": "1"}
SFSCGB_NO = {"label": "否", "value": "0"}
FT_FYBM_BRANCH_OPTIONS: list[dict[str, str]] = [
    {"label": "博亚/进出口", "value": "0"},
    {"label": "欧亚/供应链/集团", "value": "1"},
    {"label": "震宇/山水/喀什/木业", "value": "2"},
]
# 表单下拉：具体公司名；value 与分支 ROUTE 对齐（非角色，仅 select 字段）
FT_FYBM_FORM_OPTIONS: list[dict[str, str]] = [
    {"label": "新疆野马博亚商贸有限公司", "value": "0"},
    {"label": "新疆野马进出口有限公司", "value": "0"},
    {"label": "新疆野马欧亚人力资源服务有限公司", "value": "1"},
    {"label": "新疆野马供应链管理有限公司", "value": "1"},
    {"label": "野马集团有限公司", "value": "1"},
    {"label": "乌鲁木齐震宇环球商贸有限公司", "value": "2"},
    {"label": "新疆山水新能源有限公司", "value": "2"},
    {"label": "喀什野马进出口贸易有限公司", "value": "2"},
    {"label": "新疆野马木业有限公司", "value": "2"},
]
FT_FYBM_OPTIONS = FT_FYBM_BRANCH_OPTIONS
FT_CASHIER_DEPT_OPTIONS: list[dict[str, str]] = [
    {"label": "集团/震宇", "value": "0"},
    {"label": "其他费用主体", "value": "1"},
]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    return uuid.uuid4().hex


def recruitment_branch_form_fields() -> list[dict[str, Any]]:
    """招聘复杂流程必备分支字段（表单信息分组）。"""
    return [
        {
            "cascaderItem": [],
            "dicData": ZPBM_OPTIONS,
            "display": True,
            "dataType": "varchar",
            "rules": [],
            "label": "招聘部门",
            "type": "select",
            "dynamicHide": [],
            "props": {},
            "filter": {"dynamic": []},
            "indb": True,
            "prop": ZPBM_FIELD,
            "localDic": [],
            "bodyData": [],
            "isBodyParams": False,
            "dicFlag": True,
            "virtualize": True,
            "dicOption": "1",
            "span": 12,
        },
        {
            "filterable": True,
            "shieldProcessRecord": True,
            "cascaderItem": [],
            "dicData": ZWJB_OPTIONS,
            "display": True,
            "dataType": "varchar",
            "rules": [],
            "label": "职务级别",
            "type": "select",
            "dynamicHide": [],
            "props": {},
            "filter": {"dynamic": []},
            "indb": True,
            "prop": ZWJB_FIELD,
            "localDic": [],
            "bodyData": [],
            "isBodyParams": False,
            "dicFlag": True,
            "virtualize": True,
            "dicOption": "1",
            "span": 12,
        },
    ]


def _select_field(
    *,
    prop: str,
    label: str,
    options: list[dict[str, str]],
    used: set[str] | None = None,
) -> dict[str, Any]:
    if used is not None:
        used.add(prop)
    return {
        "cascaderItem": [],
        "dicData": options,
        "display": True,
        "dataType": "varchar",
        "rules": [],
        "label": label,
        "type": "select",
        "dynamicHide": [],
        "props": {},
        "filter": {"dynamic": []},
        "indb": True,
        "prop": prop,
        "localDic": [],
        "bodyData": [],
        "isBodyParams": False,
        "dicFlag": True,
        "virtualize": True,
        "dicOption": "1",
        "span": 12,
    }


def expense_branch_form_fields(*, existing_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """费用报销复杂流程分支字段；OA 已有同名字段时由 shenbi_builder 跳过重复注入。"""
    existing = existing_labels or set()
    cols: list[dict[str, Any]] = []
    used: set[str] = set()

    def maybe(prop: str, label: str, options: list[dict[str, str]]) -> None:
        if label in existing:
            return
        cols.append(_select_field(prop=prop, label=label, options=options, used=used))

    maybe(FYSY_FIELD, "费用事由", [FYSY_PROCUREMENT, FYSY_NON_PROCUREMENT])
    maybe(SFZCB_FIELD, "是否总裁办", [SFZCB_YES, SFZCB_NO])
    maybe(JEFD_FIELD, "金额分段", [JEFD_LT_10K, JEFD_GTE_10K])
    maybe(SFSJB_FIELD, "是否审计部", [SFSJB_YES, SFSJB_NO])
    maybe(CNLX_FIELD, "出纳类型", CNLX_OPTIONS)
    return cols


def amount_branch_form_fields(*, existing_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """仅金额分段分支字段（领借款/子公司报销等 serial_dag）。"""
    existing = existing_labels or set()
    cols: list[dict[str, Any]] = []
    used: set[str] = set()

    def maybe(prop: str, label: str, options: list[dict[str, str]]) -> None:
        if label in existing:
            return
        cols.append(_select_field(prop=prop, label=label, options=options, used=used))

    maybe(JEFD_FIELD, "金额分段", [JEFD_LT_10K, JEFD_GTE_10K])
    return cols


def expense_type_branch_form_fields(*, existing_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """费用类型分支字段（子公司费用报销等 serial_dag）。"""
    existing = existing_labels or set()
    cols: list[dict[str, Any]] = []
    used: set[str] = set()

    def maybe(prop: str, label: str, options: list[dict[str, str]]) -> None:
        if label in existing:
            return
        cols.append(_select_field(prop=prop, label=label, options=options, used=used))

    maybe(FYLX_FIELD, "费用类型", [FYLX_DAILY, FYLX_MAJOR])
    return cols


def subsidiary_branch_form_fields(*, existing_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """子公司 / 丝路驿站嵌套分支字段（采购借款等）。"""
    existing = existing_labels or set()
    cols: list[dict[str, Any]] = []
    used: set[str] = set()

    def maybe(prop: str, label: str, options: list[dict[str, str]]) -> None:
        if label in existing:
            return
        cols.append(_select_field(prop=prop, label=label, options=options, used=used))

    maybe(ZGS_FIELD, "子公司", ZGS_BRANCH_OPTIONS)
    maybe(SLYZ_FIELD, "丝路驿站", SLYZ_BRANCH_OPTIONS)
    maybe(JEFD_FIELD, "金额分段", [JEFD_LT_10K, JEFD_GTE_10K])
    maybe(BMMC_FIELD, "部门名称", BMMC_BRANCH_OPTIONS)
    return cols


def foreign_trade_expense_branch_form_fields(*, existing_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """外贸板块费用报销分支字段。"""
    existing = existing_labels or set()
    cols: list[dict[str, Any]] = []
    used: set[str] = set()

    def maybe(prop: str, label: str, options: list[dict[str, str]]) -> None:
        if label in existing:
            return
        cols.append(_select_field(prop=prop, label=label, options=options, used=used))

    maybe(SFSCGB_FIELD, "是否采购部", [SFSCGB_YES, SFSCGB_NO])
    maybe("fybm", "费用部门", FT_FYBM_FORM_OPTIONS)
    maybe(JEFD_FIELD, "金额分段", [JEFD_LT_10K, JEFD_GTE_10K])
    maybe("fycb", "费用出纳部门", FT_CASHIER_DEPT_OPTIONS)
    return cols


def ensure_fybm_select_field(form_info_cols: list[dict], *, used_props: set[str]) -> bool:
    """
    将 OA 已有「费用部门」字段升级为分支 select（prop=fybm，含具体公司选项）。
    发起人自选费用部门：仅表单字段，无对应审批环节。
    """
    for col in form_info_cols:
        if str(col.get("label") or "") != "费用部门":
            continue
        old_prop = str(col.get("prop") or "")
        if old_prop and old_prop != "fybm":
            used_props.discard(old_prop)
        template = _select_field(prop="fybm", label="费用部门", options=FT_FYBM_FORM_OPTIONS)
        col.update(template)
        col["display"] = True
        used_props.add("fybm")
        return True
    return False


def branch_field_props_in_tree(task_root: dict | None) -> set[str]:
    props: set[str] = set()
    for task in iter_all_tasks(task_root):
        if task.get("type") != "BRANCHTASK":
            continue
        properties = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        for cond in properties.get("branchConditionList") or []:
            prop = cond.get("fieldProp")
            if prop:
                props.add(str(prop))
    return props


def iter_all_tasks(root: dict | None) -> list[dict]:
    """深度遍历 child 链与 conditions 分支上的全部环节。"""
    out: list[dict] = []

    def walk(node: dict | None) -> None:
        if not node:
            return
        out.append(node)
        child = node.get("child")
        if isinstance(child, dict):
            walk(child)
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                walk(cond)

    walk(root)
    return out


def _encode_condition_value(options: list[dict[str, str]]) -> str:
    return json.dumps(options, ensure_ascii=False, separators=(",", ":"))


@dataclass
class _FlowCtx:
    proc_id: str
    settings: Settings
    used_keys: set[str] = field(default_factory=set)

    def _base(
        self,
        *,
        task_type: str,
        pid: str | None,
        sort: int = 0,
        task_name: str | None = None,
        task_key: str | None = None,
    ) -> dict[str, Any]:
        task_id = _new_id()
        now = _now_str()
        node: dict[str, Any] = {
            "id": task_id,
            "procId": self.proc_id,
            "pid": pid,
            "taskName": task_name,
            "taskKey": task_key,
            "type": task_type,
            "isSign": "false" if task_type == "USERTASK" else None,
            "signFormId": None,
            "sendNotify": "false" if task_type in {"USERTASK", "CCTASK"} else None,
            "notifyMsgTemp": None,
            "jumpIfRepeat": None,
            "mergeAdjacent": None,
            "jumpIfMyApplication": None,
            "sort": sort,
            "creator": "wipadmin",
            "createTime": now,
            "updator": "wipadmin",
            "updateTime": now,
            "deleteFlag": 0,
            "tenantId": self.settings.shenbi_tenant_id,
            "child": None,
            "conditions": None,
            "properties": None,
        }
        if task_type == "USERTASK":
            node["notifyMsgTemp"] = (
                "${startUserName}发起的${procName}事项，流转到${currTaskName}环节，请尽快办理！"
            )
        elif task_type == "CCTASK":
            node["notifyMsgTemp"] = "${startUserName}发起的${procName}事项，抄送了一份给您，请查阅！"
        if task_key:
            self.used_keys.add(task_key)
        return node

    def route(self, pid: str | None, *, sort: int = 0) -> dict[str, Any]:
        return self._base(task_type="ROUTE", pid=pid, sort=sort)

    def branch(
        self,
        pid: str | None,
        *,
        branch_name: str,
        branch_key: str,
        field_prop: str,
        field_name: str,
        condition_options: list[dict[str, str]],
        sort: int = 0,
    ) -> dict[str, Any]:
        node = self._base(
            task_type="BRANCHTASK",
            pid=pid,
            sort=sort,
            task_name=branch_name,
            task_key=branch_key,
        )
        node["properties"] = {
            "fromPropertyList": None,
            "buttonList": None,
            "personList": None,
            "branchConditionList": [
                {
                    "id": _new_id(),
                    "taskId": node["id"],
                    "fieldName": field_name,
                    "fieldProp": field_prop,
                    "type": "select",
                    "numberCondition": None,
                    "conditionValue": _encode_condition_value(condition_options),
                    "creator": "wipadmin",
                    "createTime": _now_str(),
                    "updator": "wipadmin",
                    "updateTime": _now_str(),
                    "deleteFlag": 0,
                    "tenantId": self.settings.shenbi_tenant_id,
                    "expression": None,
                }
            ],
            "listenerList": None,
        }
        return node

    def usertask(
        self,
        pid: str | None,
        *,
        task_name: str,
        task_key: str,
        sort: int = 0,
    ) -> dict[str, Any]:
        return self._base(
            task_type="USERTASK",
            pid=pid,
            sort=sort,
            task_name=task_name,
            task_key=task_key,
        )

    def usertask_from_guide(
        self,
        pid: str | None,
        content: str,
        *,
        index: int,
        sort: int = 0,
    ) -> dict[str, Any]:
        """指南处理节点 → USERTASK（含发起人自选 assignMember）。"""
        from core.workflow.flow_semantics import usertask_spec_from_guide_content
        from core.workflow.guide_dag_compiler import _task_key_from_name

        task_name, assign = usertask_spec_from_guide_content(content)
        key = _task_key_from_name(task_name, index=index)
        while key in self.used_keys:
            key = f"{key}_{index}"
        node = self.usertask(pid, task_name=task_name[:60], task_key=key, sort=sort)
        if assign:
            if not isinstance(node.get("properties"), dict):
                node["properties"] = {}
            node["properties"]["personList"] = [{"personType": "assignMember"}]
        return node

    def cctask(
        self,
        pid: str | None,
        *,
        task_name: str,
        task_key: str,
        sort: int = 0,
    ) -> dict[str, Any]:
        return self._base(
            task_type="CCTASK",
            pid=pid,
            sort=sort,
            task_name=task_name,
            task_key=task_key,
        )

    def start(self, *, task_name: str = "申请填报") -> dict[str, Any]:
        node = self._base(
            task_type="STARTTASK",
            pid=None,
            task_name=task_name,
            task_key="sqtb",
        )
        node["sendNotify"] = "false"
        return node

    def chain(self, nodes: list[dict]) -> dict | None:
        """将多个环节串成 child 链，返回头节点。"""
        if not nodes:
            return None
        for i in range(len(nodes) - 1):
            nodes[i]["child"] = nodes[i + 1]
            nodes[i + 1]["pid"] = nodes[i]["id"]
        return nodes[0]


def _zpbm_branch(
    ctx: _FlowCtx,
    route_id: str,
    *,
    branch_name: str,
    branch_key: str,
    dept_label: str,
    dept_value: str,
    sort: int,
    child_builder: Callable[[_FlowCtx, str], dict],
) -> dict:
    branch = ctx.branch(
        route_id,
        branch_name=branch_name,
        branch_key=branch_key,
        field_prop=ZPBM_FIELD,
        field_name="招聘部门",
        condition_options=[{"label": dept_label, "value": dept_value}],
        sort=sort,
    )
    branch["child"] = child_builder(ctx, branch["id"])
    if branch["child"]:
        branch["child"]["pid"] = branch["id"]
    return branch


def _build_cc_chain(ctx: _FlowCtx, recipients: list[str]) -> dict | None:
    """按指南抄送名单生成串行 CCTASK 链。"""
    if not recipients:
        return None
    nodes: list[dict] = []
    for idx, raw in enumerate(recipients):
        name = _cc_task_name(raw)
        key = _cc_task_key(name, idx)
        nodes.append(ctx.cctask(None, task_name=name, task_key=key))
    return ctx.chain(nodes)


def _cc_task_name(recipient: str) -> str:
    text = strip_person_name((recipient or "").strip())
    text = re.sub(r"^抄送\s*", "", text)
    if not text:
        return "抄送"
    if text.startswith("抄送"):
        return text[:60]
    return f"抄送{text}"[:60]


def _cc_task_key(task_name: str, index: int) -> str:
    from core.form.name_utils import task_key_from_name

    return task_key_from_name(task_name, index=index, prefix="cc")


def _build_tail_after_merge(
    ctx: _FlowCtx,
    merge_pid: str,
    *,
    cc_recipients: list[str] | None = None,
) -> dict:
    """汇聚后：审计/职级分支 → 副主任 → 抄送链 → 人事经理。"""
    pass_route = ctx.route(merge_pid)
    audit_route = ctx.route(None)
    pass_route["child"] = audit_route
    audit_route["pid"] = pass_route["id"]

    rank_route = ctx.route(None)
    mgr_approve = ctx.usertask(None, task_name="野马集团总经理审批", task_key="ymjtzjlsp")
    cc_gm = ctx.cctask(None, task_name="抄送野马集团总经理", task_key="csymjtzjl")
    rank_route["conditions"] = [
        ctx.branch(
            rank_route["id"],
            branch_name="经理及以上",
            branch_key="jljys",
            field_prop=ZWJB_FIELD,
            field_name="职务级别",
            condition_options=ZWJB_MANAGER_UP,
            sort=0,
        ),
        ctx.branch(
            rank_route["id"],
            branch_name="经理以下",
            branch_key="jlyx",
            field_prop=ZWJB_FIELD,
            field_name="职务级别",
            condition_options=ZWJB_MANAGER_DOWN,
            sort=1,
        ),
    ]
    rank_route["conditions"][0]["child"] = mgr_approve
    mgr_approve["pid"] = rank_route["conditions"][0]["id"]
    rank_route["conditions"][1]["child"] = cc_gm
    cc_gm["pid"] = rank_route["conditions"][1]["id"]

    audit_approve = ctx.usertask(None, task_name="野马集团总裁审批", task_key="ymjtzcsp")

    bssjb = ctx.branch(
        audit_route["id"],
        branch_name="不是审计部",
        branch_key="bssjb",
        field_prop=ZPBM_FIELD,
        field_name="招聘部门",
        condition_options=ZPBM_NOT_AUDIT,
        sort=0,
    )
    ssjb = ctx.branch(
        audit_route["id"],
        branch_name="是审计部",
        branch_key="ssjb",
        field_prop=ZPBM_FIELD,
        field_name="招聘部门",
        condition_options=ZPBM_IS_AUDIT,
        sort=1,
    )
    bssjb["child"] = rank_route
    rank_route["pid"] = bssjb["id"]
    ssjb["child"] = audit_approve
    audit_approve["pid"] = ssjb["id"]
    audit_route["conditions"] = [bssjb, ssjb]

    deputy = ctx.usertask(None, task_name="野马集团总裁办副主任审批", task_key="ymjtzcbfzrsp")
    hr = ctx.usertask(None, task_name="野马集团人事经理审批", task_key="ymjtrsjlsp")
    cc_head = _build_cc_chain(
        ctx,
        cc_recipients or ["野马集团招聘主管", "野马集团招聘专员"],
    )
    if cc_head:
        deputy["child"] = cc_head
        cc_head["pid"] = deputy["id"]
        cur = cc_head
        while cur.get("child"):
            cur = cur["child"]
        cur["child"] = hr
        hr["pid"] = cur["id"]
    else:
        deputy["child"] = hr
        hr["pid"] = deputy["id"]

    audit_route["child"] = deputy
    deputy["pid"] = audit_route["id"]

    return pass_route


def build_recruitment_task_tree(
    *,
    proc_id: str,
    settings: Settings,
    start_task_name: str = "申请填报",
) -> dict:
    """
    野马集团二线招聘需求：8 路部门并行 + 审计/职级分支 + 汇聚审批。

    拓扑与神笔样例 `复杂流程配置JSON文件.txt` 一致，环节名来自办事指南解析结果。
    """
    ctx = _FlowCtx(proc_id=proc_id, settings=settings)
    root = ctx.start(task_name=start_task_name)

    dept_route = ctx.route(root["id"])
    root["child"] = dept_route
    dept_route["pid"] = root["id"]

    def single_approval(_ctx: _FlowCtx, pid: str, name: str, key: str) -> dict:
        node = _ctx.usertask(pid, task_name=name, task_key=key)
        return node

    def finance_chain(_ctx: _FlowCtx, pid: str) -> dict:
        a = _ctx.usertask(pid, task_name="野马集团财务副经理审批", task_key="ymjtcwfjlsp")
        b = _ctx.usertask(None, task_name="野马集团财务总监审批", task_key="ymjtcwzjsp")
        a["child"] = b
        b["pid"] = a["id"]
        return a

    def purchase_chain(_ctx: _FlowCtx, pid: str) -> dict:
        a = _ctx.usertask(pid, task_name="集团非贸采购经理审批", task_key="jtfmcgjlsp")
        b = _ctx.usertask(None, task_name="集团非贸采购分管审批", task_key="jtfmcgfgsp")
        a["child"] = b
        b["pid"] = a["id"]
        return a

    dept_specs = [
        ("集团党委办公室集团工会党办", "jtdwbgsjtghdb", "集团党委办公室", "0", 0,
         lambda c, p: single_approval(c, p, "集团党委办公室集团工会党办审批", "jtdwbgsjtghdbsp")),
        ("总裁办", "zcb", "总裁办", "1", 1,
         lambda c, p: single_approval(c, p, "总裁办主任审批", "zcbzrsp")),
        ("董办", "db", "董办", "2", 2,
         lambda c, p: single_approval(c, p, "董办主任审批", "dbzrsp")),
        ("集团财务部", "jtcwb", "集团财务部", "3", 3, finance_chain),
        ("集团采购部", "jtcgb", "集团采购部", "4", 4, purchase_chain),
        ("集团纪检部", "jtjjb", "集团纪检部", "5", 5,
         lambda c, p: single_approval(c, p, "纪检部部长审批", "jjbzsp")),
        ("集团审计部", "jtsjb", "集团审计部", "6", 6,
         lambda c, p: single_approval(c, p, "审计经理审批", "sjjlsp")),
        ("集团工会", "jtgh", "集团工会", "7", 7,
         lambda c, p: single_approval(c, p, "集团工会党办审批", "jtghdbsp")),
    ]

    dept_route["conditions"] = [
        _zpbm_branch(
            ctx,
            dept_route["id"],
            branch_name=name,
            branch_key=key,
            dept_label=label,
            dept_value=value,
            sort=sort,
            child_builder=builder,
        )
        for name, key, label, value, sort, builder in dept_specs
    ]

    merge_task = ctx.usertask(dept_route["id"], task_name="总裁办主任审批2", task_key="zcbzrsp2")
    dept_route["child"] = merge_task
    merge_task["pid"] = dept_route["id"]

    tail = _build_tail_after_merge(ctx, merge_task["id"])
    merge_task["child"] = tail
    tail["pid"] = merge_task["id"]

    return root


def count_task_types(root: dict | None) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(n.get("type") for n in iter_all_tasks(root)))
