"""复杂流程（ROUTE / BRANCHTASK）程序化组装。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core.config.settings import Settings

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
    text = (recipient or "").strip()
    text = re.sub(r"^抄送\s*", "", text)
    if not text:
        return "抄送"
    if text.startswith("抄送"):
        return text[:60]
    return f"抄送{text}"[:60]


def _cc_task_key(task_name: str, index: int) -> str:
    import hashlib

    ascii_part = re.sub(r"[^\w]", "", task_name.encode("ascii", "ignore").decode())
    if ascii_part:
        return f"{ascii_part[:20].lower()}_{index}"
    return f"cc_{hashlib.md5(task_name.encode()).hexdigest()[:8]}"


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
