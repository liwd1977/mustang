"""流程节点图结构后处理：补全并列关系等。"""

from __future__ import annotations

from collections import defaultdict

from core.workflow.flow_semantics import looks_like_multi_recipient_cc
from schemas.workflow import WorkflowNodeItem


def infer_parallel_seq(nodes: list[WorkflowNodeItem]) -> None:
    """
    根据连线关系补全 parallel_seq。

    规则：
    1. 同一父节点 next_seq 中的多个直接子节点互为并列；
    2. 兜底：prev_seq 完全相同且彼此非串行上下游的节点互为并列。
    """
    if len(nodes) < 2:
        return

    by_seq = {n.seq: n for n in nodes}

    def merge_parallel(seq: int, peers: list[int]) -> None:
        node = by_seq.get(seq)
        if not node or not peers:
            return
        node.parallel_seq = sorted(set(node.parallel_seq) | set(peers))

    for parent in nodes:
        children = [c for c in parent.next_seq if c in by_seq]
        if len(children) < 2:
            continue
        for child_seq in children:
            merge_parallel(child_seq, [c for c in children if c != child_seq])

    prev_groups: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for node in nodes:
        if not node.prev_seq:
            continue
        prev_groups[tuple(sorted(node.prev_seq))].append(node.seq)

    for seqs in prev_groups.values():
        if len(seqs) < 2:
            continue
        candidates: list[int] = []
        for s in seqs:
            serial = False
            for t in seqs:
                if s == t:
                    continue
                sn, tn = by_seq[s], by_seq[t]
                if t in sn.next_seq or s in tn.next_seq:
                    serial = True
                    break
            if not serial:
                candidates.append(s)
        if len(candidates) >= 2:
            for s in candidates:
                merge_parallel(s, [x for x in candidates if x != s])


def _is_subsidiary_diamond(node: WorkflowNodeItem) -> bool:
    return node.node_type == "判断条件" and "子公司" in ((node.content or "").strip())


def _branch_has_nested_diamond(by_seq: dict[int, WorkflowNodeItem], start_seq: int, *, max_depth: int = 8) -> bool:
    """分支下游是否还有菱形条件（真实子公司分支 vs VLM 误挂抄送）。"""
    visited: set[int] = set()
    queue: list[tuple[int, int]] = [(start_seq, 0)]
    while queue:
        seq, depth = queue.pop(0)
        if seq in visited or depth > max_depth:
            continue
        visited.add(seq)
        node = by_seq.get(seq)
        if node is None:
            continue
        if node.node_type == "判断条件":
            return True
        for nxt in node.next_seq:
            queue.append((nxt, depth + 1))
    return False


def repair_misplaced_cc_under_subsidiary_diamond(nodes: list[WorkflowNodeItem]) -> list[WorkflowNodeItem]:
    """
    VLM 常将「审批 + 侧向抄送」误解析为「子公司」菱形分支（采购借款等）。
    若菱形下存在多角色抄送节点，且其余分支无后续菱形/长链，则截断为「审批 → 抄送」线性结构。
    """
    if not nodes:
        return nodes
    by_seq = {n.seq: n.model_copy(deep=True) for n in nodes}
    for diamond in sorted(by_seq.values(), key=lambda n: n.seq):
        if not _is_subsidiary_diamond(diamond):
            continue
        if len(diamond.prev_seq) != 1:
            continue
        parent = by_seq.get(diamond.prev_seq[0])
        if parent is None or parent.node_type != "处理节点":
            continue
        cc_node = next(
            (
                by_seq[s]
                for s in diamond.next_seq
                if (n := by_seq.get(s)) and (n.node_type == "抄送节点" or looks_like_multi_recipient_cc(n.content or ""))
            ),
            None,
        )
        if cc_node is None:
            continue
        # 其余分支若含嵌套菱形或较长审批链，说明是真实多路分支，不做截断
        has_real_branch = any(
            s != cc_node.seq and _branch_has_nested_diamond(by_seq, s)
            for s in diamond.next_seq
        )
        if has_real_branch:
            continue
        cc_node.node_type = "抄送节点"
        cc_node.shape = "波形"
        cc_node.prev_seq = [parent.seq]
        cc_node.next_seq = []
        cc_node.parallel_seq = []
        parent.next_seq = [cc_node.seq]
        drop = {s for s in by_seq if s >= diamond.seq and s != cc_node.seq}
        kept = [by_seq[s] for s in sorted(by_seq) if s not in drop]
        infer_parallel_seq(kept)
        return kept
    return nodes


def repair_foreign_trade_proc_rejoin(nodes: list[WorkflowNodeItem]) -> list[WorkflowNodeItem]:
    """
    外贸板块费用报销：VLM 常把「采购部抄送」直接连到稽核主管，跳过「发起人自选费用部门」菱形。
    将采购部抄送节点的下游改回费用部门菱形，保证三路会计分支与汇聚拓扑正确。
    """
    if not nodes:
        return nodes
    by_seq = {n.seq: n.model_copy(deep=True) for n in nodes}
    texts = " ".join((n.content or "") for n in by_seq.values())
    if "发起人是否是采购部" not in texts or "发起人自选费用部门" not in texts:
        return nodes

    fybm = next((n for n in by_seq.values() if (n.content or "").strip() == "发起人自选费用部门"), None)
    audit = next((n for n in by_seq.values() if (n.content or "").strip() == "稽核主管"), None)
    cgb = next((n for n in by_seq.values() if (n.content or "").strip() == "发起人是否是采购部"), None)
    if fybm is None or audit is None or cgb is None:
        return nodes

    proc_mgr = next(
        (
            by_seq[s]
            for s in cgb.next_seq
            if (n := by_seq.get(s)) and n.node_type == "处理节点" and "采购部经理" in (n.content or "")
        ),
        None,
    )
    if proc_mgr is None:
        return nodes

    cc_node = next(
        (by_seq[s] for s in proc_mgr.next_seq if (n := by_seq.get(s)) and n.node_type == "抄送节点"),
        None,
    )
    if cc_node is None:
        return nodes

    if audit.seq in cc_node.next_seq and fybm.seq not in cc_node.next_seq:
        cc_node.next_seq = [fybm.seq]
        fybm.prev_seq = sorted(set(fybm.prev_seq) | {cc_node.seq})
        audit.prev_seq = [p for p in audit.prev_seq if p != cc_node.seq]

    return [by_seq[s] for s in sorted(by_seq)]


def finalize_workflow_nodes(nodes: list[WorkflowNodeItem]) -> list[WorkflowNodeItem]:
    nodes = repair_misplaced_cc_under_subsidiary_diamond(list(nodes))
    nodes = repair_foreign_trade_proc_rejoin(nodes)
    infer_parallel_seq(nodes)
    return nodes
