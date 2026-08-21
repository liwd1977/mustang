"""外贸板块费用报销单：采购部分支 + 费用部门自选 + 金额 + 出纳分支。"""

from __future__ import annotations

from core.config.settings import Settings
from core.workflow.complex_flow_builder import (
    FT_CASHIER_DEPT_OPTIONS,
    FT_FYBM_BRANCH_OPTIONS,
    JEFD_FIELD,
    JEFD_GTE_10K,
    JEFD_LT_10K,
    SFSCGB_FIELD,
    SFSCGB_NO,
    SFSCGB_YES,
    _FlowCtx,
    _build_cc_chain,
)
from core.workflow.flow_semantics import split_cc_recipients
from core.workflow.guide_dag_compiler import GuideGraph, _extract_dept_branch_plans
from schemas.workflow import WorkflowNodeItem


def looks_like_foreign_trade_expense_dag(graph: GuideGraph) -> bool:
    texts = " ".join(graph.node_texts())
    if len(_extract_dept_branch_plans(graph)) >= 4:
        return False
    if "费用所属部门主管" in texts:
        return False
    return "发起人是否是采购部" in texts and "发起人自选费用部门" in texts


def _link(parent: dict, child: dict) -> None:
    parent["child"] = child
    child["pid"] = parent["id"]


def _tail(node: dict) -> dict:
    cur = node
    while cur.get("child"):
        cur = cur["child"]
    return cur


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
    return ctx.branch(
        route_id,
        branch_name=branch_name[:30],
        branch_key=branch_key,
        field_prop=field_prop,
        field_name=field_name,
        condition_options=condition_options,
        sort=sort,
    )


def _cc_subtree(ctx: _FlowCtx, text: str) -> dict | None:
    return _build_cc_chain(ctx, split_cc_recipients(text or ""))


def _find_seq_by_content(graph: GuideGraph, needle: str, *, node_type: str = "") -> int | None:
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        if needle not in (node.content or ""):
            continue
        if node_type and node.node_type != node_type:
            continue
        return node.seq
    return None


def _fybm_accountant_heads(graph: GuideGraph, fybm_seq: int) -> list[int]:
    """费用部门菱形下三路会计审批（排除多行公司名标签节点）。"""
    heads: list[int] = []
    for s in graph.nodes[fybm_seq].next_seq:
        n = graph.nodes.get(s)
        if n is None or n.node_type != "处理节点":
            continue
        text = (n.content or "").strip()
        if "\n" in text and "会计" not in text:
            continue
        heads.append(s)
    return sorted(heads)


def _cashier_task_seq(graph: GuideGraph, head_seq: int) -> int | None:
    """公司名标签节点 → 下游出纳处理节点。"""
    head = graph.nodes.get(head_seq)
    if head is None:
        return None
    if "出纳" in (head.content or ""):
        return head_seq
    for nxt in head.next_seq:
        n = graph.nodes.get(nxt)
        if n and n.node_type == "处理节点" and "出纳" in (n.content or ""):
            return nxt
    return None


def _find_cashier_diamond(graph: GuideGraph, amt_seq: int) -> WorkflowNodeItem | None:
    """金额分支之后的「费用部门」菱形（出纳路由，非发起人自选费用部门）。"""
    candidates = [
        n
        for n in graph.nodes.values()
        if n.node_type == "判断条件"
        and (n.content or "").strip() == "费用部门"
        and n.seq > amt_seq
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda n: n.seq)


def compile_foreign_trade_expense_from_guide(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
    start_task_name: str = "申请填报",
) -> dict:
    graph = GuideGraph.build(nodes)
    if not looks_like_foreign_trade_expense_dag(graph):
        raise ValueError("不是外贸板块费用报销 DAG")

    ctx = _FlowCtx(proc_id=proc_id, settings=settings)
    root = ctx.start(task_name=start_task_name)

    mgr_seq = _find_seq_by_content(graph, "发起人部门经理", node_type="处理节点")
    cgb_seq = _find_seq_by_content(graph, "发起人是否是采购部", node_type="判断条件")
    fybm_seq = _find_seq_by_content(graph, "发起人自选费用部门", node_type="判断条件")
    audit_seq = _find_seq_by_content(graph, "稽核主管", node_type="处理节点")
    if mgr_seq is None or cgb_seq is None or fybm_seq is None or audit_seq is None:
        raise ValueError("外贸费用报销缺少关键节点")

    mgr = _usertask(ctx, root["id"], graph.nodes[mgr_seq].content or "", index=mgr_seq)
    _link(root, mgr)

    route_cgb = ctx.route(mgr["id"])
    _link(mgr, route_cgb)

    proc_mgr_seq = next(
        s
        for s in graph.nodes[cgb_seq].next_seq
        if (n := graph.nodes.get(s)) and n.node_type == "处理节点" and "采购部经理" in (n.content or "")
    )
    cc_after_proc = next(
        (
            s
            for s in graph.nodes[proc_mgr_seq].next_seq
            if (n := graph.nodes.get(s)) and n.node_type == "抄送节点"
        ),
        None,
    )

    branch_yes = _branch(
        ctx,
        route_cgb["id"],
        branch_name="是",
        branch_key="scgb_s",
        field_prop=SFSCGB_FIELD,
        field_name="是否采购部",
        condition_options=[SFSCGB_YES],
        sort=0,
    )
    branch_no = _branch(
        ctx,
        route_cgb["id"],
        branch_name="否",
        branch_key="scgb_f",
        field_prop=SFSCGB_FIELD,
        field_name="是否采购部",
        condition_options=[SFSCGB_NO],
        sort=1,
    )

    yes_ut = _usertask(ctx, branch_yes["id"], graph.nodes[proc_mgr_seq].content or "", index=proc_mgr_seq)
    branch_yes["child"] = yes_ut
    if cc_after_proc is not None:
        cc = _cc_subtree(ctx, graph.nodes[cc_after_proc].content or "")
        if cc:
            _link(yes_ut, cc)

    route_fybm = ctx.route(route_cgb["id"])
    route_cgb["child"] = route_fybm
    route_fybm["pid"] = route_cgb["id"]
    fy_heads = _fybm_accountant_heads(graph, fybm_seq)
    fy_conditions: list[dict] = []
    for sort, head_seq in enumerate(sorted(fy_heads)):
        opt = (
            FT_FYBM_BRANCH_OPTIONS[sort]
            if sort < len(FT_FYBM_BRANCH_OPTIONS)
            else {"label": f"费用部门{sort + 1}", "value": str(sort)}
        )
        br = _branch(
            ctx,
            route_fybm["id"],
            branch_name=str(opt["label"])[:30],
            branch_key=f"fybm{sort}",
            field_prop="fybm",
            field_name="费用部门",
            condition_options=[opt],
            sort=sort,
        )
        ut = _usertask(ctx, br["id"], graph.nodes[head_seq].content or "", index=head_seq)
        br["child"] = ut
        fy_conditions.append(br)
    route_fybm["conditions"] = fy_conditions
    route_cgb["conditions"] = [branch_yes, branch_no]

    audit = _usertask(ctx, route_fybm["id"], graph.nodes[audit_seq].content or "", index=audit_seq)
    route_fybm["child"] = audit
    tail = audit

    cc_after_audit = next(
        (
            s
            for s in graph.nodes[audit_seq].next_seq
            if (n := graph.nodes.get(s)) and n.node_type == "抄送节点"
        ),
        None,
    )
    if cc_after_audit is not None:
        cc = _cc_subtree(ctx, graph.nodes[cc_after_audit].content or "")
        if cc:
            _link(tail, cc)
            tail = _tail(cc)

    lead_seq = _find_seq_by_content(graph, "发起人部门分管领导", node_type="处理节点")
    if lead_seq is not None:
        lead = _usertask(ctx, tail["id"], graph.nodes[lead_seq].content or "", index=lead_seq)
        _link(tail, lead)
        tail = lead

    gm_seq = _find_seq_by_content(graph, "野马集团总经理", node_type="处理节点")
    if gm_seq is None:
        gm_seq = _find_seq_by_content(graph, "总经理", node_type="处理节点")
    if gm_seq is not None:
        gm = _usertask(ctx, tail["id"], graph.nodes[gm_seq].content or "", index=gm_seq)
        _link(tail, gm)
        tail = gm

    amt_seq = _find_seq_by_content(graph, "金额", node_type="判断条件")
    if amt_seq is not None:
        route_amt = ctx.route(tail["id"])
        _link(tail, route_amt)
        lt_seq = gte_seq = None
        for s in graph.nodes[amt_seq].next_seq:
            n = graph.nodes.get(s)
            if n is None:
                continue
            if n.node_type == "抄送节点":
                lt_seq = s
            elif n.node_type == "处理节点" and "总经理" in (n.content or ""):
                gte_seq = s
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
        if lt_seq is not None:
            cc = _cc_subtree(ctx, graph.nodes[lt_seq].content or "")
            if cc:
                branch_lt["child"] = cc
                cc["pid"] = branch_lt["id"]
        if gte_seq is not None:
            ut = _usertask(ctx, branch_gte["id"], graph.nodes[gte_seq].content or "", index=gte_seq)
            branch_gte["child"] = ut
            ut["pid"] = branch_gte["id"]
        route_amt["conditions"] = [branch_lt, branch_gte]
        tail = route_amt

        cashier_diamond = _find_cashier_diamond(graph, amt_seq)
        if cashier_diamond is not None:
            route_cashier = ctx.route(route_amt["id"])
            route_amt["child"] = route_cashier
            tail = _attach_cashier_branches(ctx, graph, route_cashier, cashier_diamond)
    else:
        cashier_diamond = next(
            (
                n
                for n in graph.nodes.values()
                if n.node_type == "判断条件"
                and (n.content or "").strip() == "费用部门"
                and n.seq > audit_seq
            ),
            None,
        )
        if cashier_diamond is not None:
            route_cashier = ctx.route(tail["id"])
            _link(tail, route_cashier)
            _attach_cashier_branches(ctx, graph, route_cashier, cashier_diamond)

    return root


def _attach_cashier_branches(
    ctx: _FlowCtx,
    graph: GuideGraph,
    route_cashier: dict,
    cashier_diamond: WorkflowNodeItem,
) -> dict:
    cash_heads = [
        s
        for s in cashier_diamond.next_seq
        if (n := graph.nodes.get(s)) and n.node_type == "处理节点"
    ]
    cash_conditions: list[dict] = []
    for sort, head_seq in enumerate(sorted(cash_heads)):
        opt = (
            FT_CASHIER_DEPT_OPTIONS[sort]
            if sort < len(FT_CASHIER_DEPT_OPTIONS)
            else {"label": f"出纳分支{sort + 1}", "value": str(sort)}
        )
        br = _branch(
            ctx,
            route_cashier["id"],
            branch_name=str(opt["label"])[:30],
            branch_key=f"cnbm{sort}",
            field_prop="fycb",
            field_name="费用出纳部门",
            condition_options=[opt],
            sort=sort,
        )
        cash_seq = _cashier_task_seq(graph, head_seq)
        if cash_seq is not None:
            ut = _usertask(ctx, br["id"], graph.nodes[cash_seq].content or "", index=cash_seq)
            br["child"] = ut
            ut["pid"] = br["id"]
        cash_conditions.append(br)
    route_cashier["conditions"] = cash_conditions
    return route_cashier
