"""集团费用报销单：条件 DAG → 神笔环节树。"""

from __future__ import annotations

import re
from typing import Any

from core.config.settings import Settings
from core.workflow.complex_flow_builder import (
    CNLX_FIELD,
    CNLX_OPTIONS,
    FYSY_FIELD,
    FYSY_NON_PROCUREMENT,
    FYSY_PROCUREMENT,
    JEFD_FIELD,
    JEFD_GTE_10K,
    JEFD_LT_10K,
    SFZCB_FIELD,
    SFZCB_NO,
    SFZCB_YES,
    SFSJB_FIELD,
    SFSJB_NO,
    SFSJB_YES,
    _FlowCtx,
    _build_cc_chain,
)
from core.workflow.flow_semantics import split_cc_recipients, strip_person_name
from core.workflow.guide_dag_compiler import (
    GuideGraph,
    _extract_dept_branch_plans,
    _is_guide_cc_node,
)
from schemas.workflow import WorkflowNodeItem


def looks_like_expense_reimbursement_dag(graph: GuideGraph) -> bool:
    if len(_extract_dept_branch_plans(graph)) >= 4:
        return False
    texts = " ".join(graph.node_texts())
    diamonds = sum(1 for n in graph.nodes.values() if n.node_type == "判断条件")
    return (
        diamonds >= 3
        and "费用所属部门主管" in texts
        and "出纳" in texts
        and ("是否是采购费用" in texts or "采购费用" in texts)
    )


def _link(parent: dict, child: dict) -> None:
    parent["child"] = child
    child["pid"] = parent["id"]


def _usertask(ctx: _FlowCtx, pid: str | None, content: str, *, index: int) -> dict:
    return ctx.usertask_from_guide(pid, content, index=index)


def _branch(
    ctx: _FlowCtx,
    route_id: str,
    *,
    branch_name: str,
    branch_key: str,
    field_prop: str,
    field_name: str,
    condition_options: list[dict[str, str]],
    sort: int,
) -> dict:
    node = ctx.branch(
        route_id,
        branch_name=branch_name[:30],
        branch_key=branch_key,
        field_prop=field_prop,
        field_name=field_name,
        condition_options=condition_options,
        sort=sort,
    )
    return node


def _cc_subtree(ctx: _FlowCtx, text: str) -> dict | None:
    recipients = split_cc_recipients(text or "")
    return _build_cc_chain(ctx, recipients)


def _is_president_role(text: str) -> bool:
    """「野马集团总裁」类角色（不含总裁办）。"""
    role = strip_person_name(text)
    if re.search(r"集团总裁(?!办)", role):
        return True
    return role.endswith("总裁") and "总经理" not in role


def _is_gm_role(text: str) -> bool:
    """「野马集团总经理」类角色。"""
    role = strip_person_name(text)
    return "总经理" in role and not _is_president_role(role)


def _amount_branch_followup(
    ctx: _FlowCtx,
    node: WorkflowNodeItem,
    *,
    lt_10k: bool,
) -> dict | None:
    """
    金额分支下游：小于1万 → 波形/知会 → 抄送；大于等于1万 → 长方形 → 审批。

    VLM 可能将 >=1万 的长方形误标为波形/抄送，编译层按金额分支强制纠正。
    """
    if lt_10k:
        return _cc_subtree(ctx, node.content or "")
    return _usertask(ctx, None, node.content or "", index=node.seq)


def _procurement_yes_chain(ctx: _FlowCtx, graph: GuideGraph) -> dict | None:
    cc_node = next(
        (n for n in graph.nodes.values() if _is_guide_cc_node(n) and "采购部内勤" in (n.content or "")),
        None,
    )
    mgr_node = next(
        (n for n in graph.nodes.values() if n.node_type == "处理节点" and "采购部经理" in (n.content or "")),
        None,
    )
    head: dict | None = None
    tail: dict | None = None
    if cc_node:
        head = _cc_subtree(ctx, cc_node.content or "")
        tail = head
        while tail and tail.get("child"):
            tail = tail["child"]
    if mgr_node:
        ut = _usertask(ctx, None, mgr_node.content or "", index=mgr_node.seq)
        if tail:
            _link(tail, ut)
        else:
            head = ut
    return head


def _audit_subtree(ctx: _FlowCtx, graph: GuideGraph, *, lt_10k: bool) -> dict | None:
    amount_hint = "小于1万" if lt_10k else "大于等于1万"
    audit_diamond = next(
        (
            n
            for n in graph.nodes.values()
            if n.node_type == "判断条件"
            and n.content == "审计部"
            and any(
                graph.nodes.get(p) and amount_hint in (graph.nodes[p].content or "")
                for p in n.prev_seq
            )
        ),
        None,
    )
    if audit_diamond is None:
        audit_nodes = [n for n in graph.nodes.values() if n.node_type == "判断条件" and n.content == "审计部"]
        audit_diamond = audit_nodes[0 if lt_10k else min(1, len(audit_nodes) - 1)] if audit_nodes else None
    if audit_diamond is None:
        return None

    downstream = [graph.nodes[s] for s in audit_diamond.next_seq if s in graph.nodes]
    gm_node = next((n for n in downstream if _is_gm_role(n.content or "")), None)
    president_node = next((n for n in downstream if _is_president_role(n.content or "")), None)

    route = ctx.route(None)
    branch_no = _branch(
        ctx,
        route["id"],
        branch_name="不是审计部",
        branch_key="bssjb" if lt_10k else "bssjb2",
        field_prop=SFSJB_FIELD,
        field_name="是否审计部",
        condition_options=[SFSJB_NO],
        sort=0,
    )
    branch_yes = _branch(
        ctx,
        route["id"],
        branch_name="是审计部",
        branch_key="sfsjb" if lt_10k else "sfsjb2",
        field_prop=SFSJB_FIELD,
        field_name="是否审计部",
        condition_options=[SFSJB_YES],
        sort=1,
    )
    if gm_node:
        follow = _amount_branch_followup(ctx, gm_node, lt_10k=lt_10k)
        if follow:
            branch_no["child"] = follow
            follow["pid"] = branch_no["id"]
    if president_node:
        follow = _amount_branch_followup(ctx, president_node, lt_10k=lt_10k)
        if follow:
            branch_yes["child"] = follow
            follow["pid"] = branch_yes["id"]
    route["conditions"] = [branch_no, branch_yes]
    return route


def _linear_usertasks(ctx: _FlowCtx, graph: GuideGraph) -> dict | None:
    markers = ("费用所属部门主管", "费用会计", "稽核主管", "费用所属分管领导", "分管领导")
    seqs = [
        n.seq
        for n in sorted(graph.nodes.values(), key=lambda x: x.seq)
        if n.node_type == "处理节点" and any(m in (n.content or "") for m in markers)
    ]
    head: dict | None = None
    tail: dict | None = None
    for seq in seqs:
        node = graph.nodes.get(seq)
        if not node:
            continue
        ut = _usertask(ctx, None, node.content or "", index=seq)
        if head is None:
            head = ut
        if tail is not None:
            _link(tail, ut)
        tail = ut
    return head


def _cashier_end_route(ctx: _FlowCtx, graph: GuideGraph, cashier: dict) -> None:
    dept_cc_nodes = sorted(
        [n for n in graph.nodes.values() if n.seq in {24, 25} and _is_guide_cc_node(n)],
        key=lambda n: n.seq,
    )
    cashier_nodes = sorted(
        [
            n
            for n in graph.nodes.values()
            if n.node_type == "处理节点"
            and "出纳" in (n.content or "")
            and n.seq >= 24
            and (n.content or "").strip() != "出纳"
        ],
        key=lambda n: n.seq,
    )
    if len(dept_cc_nodes) < 2 or len(cashier_nodes) < 2:
        return

    route = ctx.route(cashier["id"])
    _link(cashier, route)
    branches: list[dict] = []
    for sort, (cc_n, cash_n) in enumerate(zip(dept_cc_nodes, cashier_nodes, strict=False)):
        opt = CNLX_OPTIONS[sort] if sort < len(CNLX_OPTIONS) else {"label": f"出纳分支{sort + 1}", "value": str(sort)}
        branch = _branch(
            ctx,
            route["id"],
            branch_name=str(opt["label"])[:30],
            branch_key=f"cnlx{sort}",
            field_prop=CNLX_FIELD,
            field_name="出纳类型",
            condition_options=[opt],
            sort=sort,
        )
        chain_head = _cc_subtree(ctx, cc_n.content or "")
        cash_ut = _usertask(ctx, None, cash_n.content or "", index=cash_n.seq)
        if chain_head:
            tail = chain_head
            while tail.get("child"):
                tail = tail["child"]
            _link(tail, cash_ut)
            branch["child"] = chain_head
            chain_head["pid"] = branch["id"]
        else:
            branch["child"] = cash_ut
            cash_ut["pid"] = branch["id"]
        branches.append(branch)
    route["conditions"] = branches


def compile_expense_from_guide(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
    start_task_name: str = "申请填报",
) -> dict:
    graph = GuideGraph.build(nodes)
    if not looks_like_expense_reimbursement_dag(graph):
        raise ValueError("不是费用报销条件 DAG")

    ctx = _FlowCtx(proc_id=proc_id, settings=settings)
    root = ctx.start(task_name=start_task_name)

    prefix = next(
        (n for n in sorted(graph.nodes.values(), key=lambda x: x.seq) if "自选" in (n.content or "") and n.node_type == "处理节点"),
        graph.nodes.get(3),
    )
    if prefix is None:
        raise ValueError("费用报销流程缺少发起人自选部门经理节点")

    dept_mgr = _usertask(ctx, root["id"], prefix.content or "", index=prefix.seq)
    _link(root, dept_mgr)

    route_zcb = ctx.route(dept_mgr["id"])
    _link(dept_mgr, route_zcb)
    deputy_cc = next(
        (n for n in graph.nodes.values() if _is_guide_cc_node(n) and "副主任" in (n.content or "")),
        None,
    )
    branch_zcb_no = _branch(
        ctx,
        route_zcb["id"],
        branch_name="不是总裁办",
        branch_key="bzcb",
        field_prop=SFZCB_FIELD,
        field_name="是否总裁办",
        condition_options=[SFZCB_NO],
        sort=0,
    )
    branch_zcb_yes = _branch(
        ctx,
        route_zcb["id"],
        branch_name="是总裁办",
        branch_key="szcb",
        field_prop=SFZCB_FIELD,
        field_name="是否总裁办",
        condition_options=[SFZCB_YES],
        sort=1,
    )
    if deputy_cc:
        cc = _cc_subtree(ctx, deputy_cc.content or "")
        if cc:
            branch_zcb_yes["child"] = cc
            cc["pid"] = branch_zcb_yes["id"]
    route_zcb["conditions"] = [branch_zcb_no, branch_zcb_yes]

    route_proc = ctx.route(route_zcb["id"])
    _link(route_zcb, route_proc)
    branch_proc_yes = _branch(
        ctx,
        route_proc["id"],
        branch_name="采购费用",
        branch_key="cgfy",
        field_prop=FYSY_FIELD,
        field_name="费用事由",
        condition_options=[FYSY_PROCUREMENT],
        sort=0,
    )
    branch_proc_no = _branch(
        ctx,
        route_proc["id"],
        branch_name="非采购费用",
        branch_key="fcgfy",
        field_prop=FYSY_FIELD,
        field_name="费用事由",
        condition_options=[FYSY_NON_PROCUREMENT],
        sort=1,
    )
    proc_chain = _procurement_yes_chain(ctx, graph)
    if proc_chain:
        branch_proc_yes["child"] = proc_chain
        proc_chain["pid"] = branch_proc_yes["id"]
    route_proc["conditions"] = [branch_proc_yes, branch_proc_no]

    linear_head = _linear_usertasks(ctx, graph)
    if linear_head is None:
        raise ValueError("费用报销流程缺少串行审批链")
    _link(route_proc, linear_head)
    linear_head["pid"] = route_proc["id"]

    tail = linear_head
    while tail.get("child"):
        tail = tail["child"]

    notify_cc = next(
        (
            n
            for n in graph.nodes.values()
            if _is_guide_cc_node(n)
            and "财务总监" in (n.content or "")
            and n.seq < 20
        ),
        None,
    )
    if notify_cc:
        cc = _cc_subtree(ctx, notify_cc.content or "")
        if cc:
            _link(tail, cc)
            cc["pid"] = tail["id"]
            tail = cc
            while tail.get("child"):
                tail = tail["child"]

    route_amt = ctx.route(tail["id"])
    _link(tail, route_amt)
    branch_lt = _branch(
        ctx,
        route_amt["id"],
        branch_name="小于1万",
        branch_key="xy1w",
        field_prop=JEFD_FIELD,
        field_name="金额分段",
        condition_options=[JEFD_LT_10K],
        sort=0,
    )
    branch_gte = _branch(
        ctx,
        route_amt["id"],
        branch_name="大于等于1万",
        branch_key="dydy1w",
        field_prop=JEFD_FIELD,
        field_name="金额分段",
        condition_options=[JEFD_GTE_10K],
        sort=1,
    )
    audit_lt = _audit_subtree(ctx, graph, lt_10k=True)
    audit_gte = _audit_subtree(ctx, graph, lt_10k=False)
    if audit_lt:
        branch_lt["child"] = audit_lt
        audit_lt["pid"] = branch_lt["id"]
    if audit_gte:
        branch_gte["child"] = audit_gte
        audit_gte["pid"] = branch_gte["id"]
    route_amt["conditions"] = [branch_lt, branch_gte]

    cashier_node = next(
        (n for n in graph.nodes.values() if n.node_type == "处理节点" and (n.content or "").strip() == "出纳"),
        None,
    )
    if cashier_node is None:
        raise ValueError("费用报销流程缺少出纳节点")
    cashier = _usertask(ctx, route_amt["id"], cashier_node.content or "", index=cashier_node.seq)
    _link(route_amt, cashier)

    _cashier_end_route(ctx, graph, cashier)
    return root


def summarize_expense_branch_bindings(nodes: list[WorkflowNodeItem]) -> list[dict[str, Any]]:
    graph = GuideGraph.build(nodes)
    bindings: list[dict[str, Any]] = []
    for n in sorted(graph.nodes.values(), key=lambda x: x.seq):
        if n.node_type != "判断条件":
            continue
        bindings.append(
            {
                "guide_seq": n.seq,
                "condition_text": (n.content or "").strip(),
                "next_seq": list(n.next_seq),
            }
        )
    return bindings
