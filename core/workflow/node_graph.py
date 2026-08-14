"""流程节点图结构后处理：补全并列关系等。"""

from __future__ import annotations

from collections import defaultdict

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


def finalize_workflow_nodes(nodes: list[WorkflowNodeItem]) -> list[WorkflowNodeItem]:
    infer_parallel_seq(nodes)
    return nodes
