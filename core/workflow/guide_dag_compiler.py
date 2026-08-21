"""办事指南 DAG → 神笔环节树编译（需求加工层 / P2）。

输入：`parse_result` 中的节点 DAG（VLM 对 Word 流程图的忠实提取，不在此层改写）。
输出：神笔 `wfSimpleTaskInfo`（拓扑、分支字段绑定、环节命名等业务规则在此实现）。

加工层环节名**以 parse 节点文字为准**，仅允许文档规定的通用变换（去人名、职务标准名、汇聚重名加 2 等），
**不得**擅自替换职务/角色。与 `vlm_recognizer.py`（识别）职责分离；后续 flow_rules 亦不回写 parse_result。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from core.config.settings import Settings
from core.workflow.complex_flow_builder import (
    ZPBM_FIELD,
    ZWJB_FIELD,
    _FlowCtx,
    _build_cc_chain,
    _build_tail_after_merge,
    _zpbm_branch,
    count_task_types,
    iter_all_tasks,
)
from core.workflow.flow_semantics import (
    guide_nodes_to_semantic_steps,
    looks_like_multi_recipient_cc,
    split_cc_recipients,
    strip_person_name,
)
from core.workflow.node_graph import finalize_workflow_nodes
from schemas.workflow import WorkflowNodeItem

# P2b：招聘部门分支 → zpbm 选项（仅拓扑/分支键/表单绑定；环节名取自指南节点）
RECRUITMENT_DEPT_SPECS: list[dict[str, Any]] = [
    {
        "patterns": (r"党委办公室", r"党委"),
        "branch_name": "集团党委办公室集团工会党办",
        "branch_key": "jtdwbgsjtghdb",
        "dept_label": "集团党委办公室",
        "dept_value": "0",
        "approval_key": "jtdwbgsjtghdbsp",
    },
    {
        "patterns": (r"^总裁办$", r"总裁办(?!主任)"),
        "branch_name": "总裁办",
        "branch_key": "zcb",
        "dept_label": "总裁办",
        "dept_value": "1",
        "approval_key": "zcbzrsp",
    },
    {
        "patterns": (r"^董办$", r"董办"),
        "branch_name": "董办",
        "branch_key": "db",
        "dept_label": "董办",
        "dept_value": "2",
        "approval_key": "dbzrsp",
    },
    {
        "patterns": (r"财务部", r"财务"),
        "branch_name": "集团财务部",
        "branch_key": "jtcwb",
        "dept_label": "集团财务部",
        "dept_value": "3",
        "approval_keys": ("ymjtcwfjlsp", "ymjtcwzjsp"),
    },
    {
        "patterns": (r"采购", r"非贸"),
        "branch_name": "集团采购部",
        "branch_key": "jtcgb",
        "dept_label": "集团采购部",
        "dept_value": "4",
        "approval_keys": ("jtfmcgjlsp", "jtfmcgfgsp"),
    },
    {
        "patterns": (r"纪检",),
        "branch_name": "集团纪检部",
        "branch_key": "jtjjb",
        "dept_label": "集团纪检部",
        "dept_value": "5",
        "approval_key": "jjbzsp",
    },
    {
        "patterns": (r"审计",),
        "branch_name": "集团审计部",
        "branch_key": "jtsjb",
        "dept_label": "集团审计部",
        "dept_value": "6",
        "approval_key": "sjjlsp",
    },
    {
        "patterns": (r"^集团工会$", r"集团工会(?!党办)"),
        "branch_name": "集团工会",
        "branch_key": "jtgh",
        "dept_label": "集团工会",
        "dept_value": "7",
        "approval_key": "jtghdbsp",
    },
]

_MERGE_HINTS = (r"总裁办主任审批2", r"总裁办主任.*2", r"汇聚", r"合并")

# 环节名末尾若为人名（2~4 汉字且非职务词），剥离；具体人员由神笔运行时维护
def _strip_person_name(text: str) -> str:
    return strip_person_name(text)


def _task_name_from_content(text: str) -> str:
    from core.workflow.flow_semantics import usertask_spec_from_guide_content

    name, _assign = usertask_spec_from_guide_content(text)
    return name


def _merge_task_name(content: str) -> str:
    """汇聚 USERTASK（taskKey 含 2）：parse 文字 + 强制「审批2」后缀（神笔重名规律）。"""
    name = _task_name_from_content(content or "总裁办主任")
    if re.search(r"审批2$", name):
        return name[:60]
    if name.endswith("审批"):
        return f"{name}2"[:60]
    return f"{name}审批2"[:60]


def _task_key_from_name(name: str, *, index: int) -> str:
    from core.form.name_utils import task_key_from_name as _tk

    return _tk(name, index=index)


@dataclass
class GuideGraph:
    """办事指南节点 DAG（P2a）。"""

    nodes: dict[int, WorkflowNodeItem] = field(default_factory=dict)

    @classmethod
    def build(cls, nodes: list[WorkflowNodeItem]) -> GuideGraph:
        finalized = finalize_workflow_nodes(list(nodes))
        return cls({n.seq: n for n in finalized})

    def fanout_groups(self) -> list[list[WorkflowNodeItem]]:
        """同一前驱的多条出边 → 并行分支组。"""
        by_prev: dict[tuple[int, ...], list[WorkflowNodeItem]] = defaultdict(list)
        for node in self.nodes.values():
            if not node.prev_seq:
                continue
            key = tuple(sorted(node.prev_seq))
            by_prev[key].append(node)

        groups: list[list[WorkflowNodeItem]] = []
        for siblings in by_prev.values():
            if len(siblings) < 2:
                continue
            serial = False
            for a in siblings:
                for b in siblings:
                    if a.seq == b.seq:
                        continue
                    if b.seq in a.next_seq or a.seq in b.next_seq:
                        serial = True
                        break
                if serial:
                    break
            if not serial:
                groups.append(sorted(siblings, key=lambda n: n.seq))
        groups.sort(key=lambda g: (-len(g), g[0].seq if g else 0))
        return groups

    def node_texts(self) -> list[str]:
        return [(n.content or "").strip() for n in sorted(self.nodes.values(), key=lambda x: x.seq)]


@dataclass
class _DeptBranchPlan:
    spec: dict[str, Any]
    task_names: list[str]
    sort: int
    guide_seqs: list[int] = field(default_factory=list)
    guide_contents: list[str] = field(default_factory=list)


def _specs_by_dept_value() -> list[dict[str, Any]]:
    return sorted(RECRUITMENT_DEPT_SPECS, key=lambda s: int(s["dept_value"]))


def _match_patterns(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _resolve_dept_spec(*texts: str) -> dict[str, Any] | None:
    """按部门标签/节点文字匹配 zpbm 分支 spec（最长优先，避免「工会」误匹配）。"""
    best: dict[str, Any] | None = None
    best_score = -1
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        for spec in RECRUITMENT_DEPT_SPECS:
            labels = (spec["dept_label"], spec["branch_name"])
            for label in labels:
                if text == label or label in text or text in label:
                    score = len(label) * 10 + (10 if text == label else 0)
                    if score > best_score:
                        best_score = score
                        best = spec
            for pat in spec.get("patterns") or ():
                if re.search(pat, text):
                    score = 80 + len(pat)
                    if score > best_score:
                        best_score = score
                        best = spec
    return best


def _find_merge_seqs(graph: GuideGraph) -> set[int]:
    """汇聚环节：多前驱的处理/判断节点，或名称含「审批2/汇聚」。"""
    merge: set[int] = set()
    for node in graph.nodes.values():
        text = (node.content or "").strip()
        if len(node.prev_seq) >= 2 and node.node_type in {"处理节点", "判断条件"}:
            merge.add(node.seq)
        if _match_patterns(text, _MERGE_HINTS):
            merge.add(node.seq)
    return merge


def _branch_processing_nodes(
    graph: GuideGraph,
    head: WorkflowNodeItem,
    merge_seqs: set[int],
) -> list[WorkflowNodeItem]:
    """从 fan-out 分支头向下收集本支路上的处理节点（不含汇聚之后）。"""
    collected: list[WorkflowNodeItem] = []
    seen: set[int] = set()

    def walk(seq: int) -> None:
        if seq in seen or seq in merge_seqs:
            return
        seen.add(seq)
        node = graph.nodes.get(seq)
        if not node:
            return
        if node.node_type == "处理节点":
            collected.append(node)
        if len(node.next_seq) == 1:
            walk(node.next_seq[0])
        elif not node.next_seq:
            return
        else:
            for nxt in node.next_seq:
                if nxt not in merge_seqs:
                    walk(nxt)

    walk(head.seq)
    return collected


def _extract_dept_branch_plans(graph: GuideGraph) -> list[_DeptBranchPlan]:
    fanouts = graph.fanout_groups()
    dept_fanout = next((g for g in fanouts if len(g) >= 4), None)
    if not dept_fanout:
        return []

    merge_seqs = _find_merge_seqs(graph)
    heads = sorted(dept_fanout, key=lambda n: n.seq)
    specs_ordered = _specs_by_dept_value()
    use_position = len(heads) == len(specs_ordered)
    plans: list[_DeptBranchPlan] = []
    used_dept_values: set[str] = set()

    for idx, head in enumerate(heads):
        head_text = (head.content or "").strip()
        proc_nodes = _branch_processing_nodes(graph, head, merge_seqs)

        if use_position:
            spec = specs_ordered[idx]
        else:
            spec = _resolve_dept_spec(head_text, *(n.content or "" for n in proc_nodes))
            if spec and str(spec["dept_value"]) in used_dept_values:
                spec = None
        if not spec:
            continue
        used_dept_values.add(str(spec["dept_value"]))

        if proc_nodes:
            task_names = [_task_name_from_content(n.content or "") for n in proc_nodes]
            guide_seqs = [n.seq for n in proc_nodes]
            guide_contents = [(n.content or "").strip() for n in proc_nodes]
        else:
            task_names = [_task_name_from_content(head_text)]
            guide_seqs = [head.seq]
            guide_contents = [head_text]

        plans.append(
            _DeptBranchPlan(
                spec=spec,
                task_names=task_names,
                sort=int(spec["dept_value"]),
                guide_seqs=guide_seqs,
                guide_contents=guide_contents,
            )
        )

    plans.sort(key=lambda p: p.sort)
    return plans


def summarize_recruitment_branch_plans(nodes: list[WorkflowNodeItem]) -> list[dict[str, Any]]:
    """8 路分支绑定摘要（供 payload / UI 核对）。"""
    graph = GuideGraph.build(nodes)
    return [
        {
            "dept_label": str(p.spec["dept_label"]),
            "dept_value": str(p.spec["dept_value"]),
            "guide_seqs": p.guide_seqs,
            "guide_contents": p.guide_contents,
            "task_names": p.task_names,
        }
        for p in _extract_dept_branch_plans(graph)
    ]


def _find_post_merge_deputy_seq(graph: GuideGraph) -> int | None:
    """汇聚后多路汇入的副主任/总裁办审批节点（抄送挂在此节点之后）。"""
    merge_seqs = _find_merge_seqs(graph)
    candidates: list[tuple[int, str]] = []
    for node in graph.nodes.values():
        if node.seq in merge_seqs:
            continue
        if node.node_type != "处理节点" or len(node.prev_seq) < 2:
            continue
        text = (node.content or "").strip()
        if text:
            candidates.append((node.seq, text))
    if not candidates:
        return None
    for seq, text in sorted(candidates, key=lambda x: -x[0]):
        if "副主任" in text or ("总裁办" in text and "主任" in text):
            return seq
    return sorted(candidates, key=lambda x: x[0])[-1][0]


def _is_guide_cc_node(node: WorkflowNodeItem) -> bool:
    from core.workflow.flow_semantics import looks_like_multi_recipient_cc

    text = (node.content or "").strip()
    if node.node_type == "抄送节点":
        return True
    if (node.shape or "").strip() == "波形":
        return bool(text) and "审批" not in text
    if looks_like_multi_recipient_cc(text):
        return True
    return bool(text) and "抄送" in text and "审批" not in text


def _extract_cc_recipients_from_graph(graph: GuideGraph) -> list[str]:
    """副主任节点之后、人事经理之前的抄送（不含职级分支上的抄送总经理）。"""
    deputy_seq = _find_post_merge_deputy_seq(graph)
    if deputy_seq is None:
        return []

    recipients: list[str] = []
    seen_names: set[str] = set()
    seen_seqs: set[int] = set()

    def collect(node: WorkflowNodeItem) -> None:
        if not _is_guide_cc_node(node):
            return
        for part in split_cc_recipients(node.content or ""):
            if part not in seen_names:
                seen_names.add(part)
                recipients.append(part)

    def walk_from_deputy(seq: int) -> None:
        if seq in seen_seqs:
            return
        seen_seqs.add(seq)
        node = graph.nodes.get(seq)
        if not node:
            return
        if node.node_type == "处理节点" and seq != deputy_seq:
            return
        if seq != deputy_seq:
            collect(node)
        for nxt in node.next_seq:
            walk_from_deputy(nxt)

    deputy = graph.nodes.get(deputy_seq)
    if not deputy:
        return recipients

    for nxt in deputy.next_seq:
        walk_from_deputy(nxt)
    for node in graph.nodes.values():
        if node.prev_seq == [deputy_seq] and _is_guide_cc_node(node):
            collect(node)
    return recipients


def _find_merge_name(graph: GuideGraph) -> str:
    merge_seqs = _find_merge_seqs(graph)
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        if node.seq not in merge_seqs:
            continue
        text = (node.content or "").strip()
        if text:
            return _merge_task_name(text)
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        text = (node.content or "").strip()
        if _match_patterns(text, _MERGE_HINTS):
            return _merge_task_name(text)
    return "总裁办主任审批2"


def _tail_task_overrides(graph: GuideGraph) -> dict[str, str]:
    """taskKey → 指南中的环节名（汇聚后链）。"""
    overrides: dict[str, str] = {}
    merge_seqs = _find_merge_seqs(graph)
    if not merge_seqs:
        return overrides

    tail_nodes: list[WorkflowNodeItem] = []
    seen_seqs: set[int] = set()

    def walk(seq: int) -> None:
        if seq in seen_seqs:
            return
        seen_seqs.add(seq)
        node = graph.nodes.get(seq)
        if not node:
            return
        if node.node_type == "处理节点":
            tail_nodes.append(node)
        elif node.node_type == "抄送节点":
            tail_nodes.append(node)
        for nxt in node.next_seq:
            walk(nxt)

    for mseq in merge_seqs:
        merge_node = graph.nodes.get(mseq)
        if not merge_node:
            continue
        for nxt in merge_node.next_seq:
            walk(nxt)

    key_hints = (
        ("ymjtzcbfzrsp", ("总裁办副主任",)),
        ("ymjtrsjlsp", ("人事经理",)),
        ("ymjtzjlsp", ("总经理审批", "总经理")),
        ("ymjtzcsp", ("集团总裁", "总裁审批")),
        ("csymjtzjl", ("抄送", "总经理")),
    )
    texts = [(n.content or "").strip() for n in tail_nodes]
    for task_key, hints in key_hints:
        for text in texts:
            if not text or not any(h in text for h in hints):
                continue
            if task_key == "ymjtzcsp" and "副主任" in text:
                continue
            if task_key == "ymjtzcsp" and "总裁办" in text and "集团总裁" not in text:
                continue
            overrides[task_key] = _task_name_from_content(text)
            break
    return overrides


def _apply_task_name_overrides(root: dict, overrides: dict[str, str]) -> None:
    if not overrides:
        return

    def walk(node: dict | None) -> None:
        if not node:
            return
        key = str(node.get("taskKey") or "")
        if key in overrides:
            node["taskName"] = overrides[key]
        for cond in node.get("conditions") or []:
            if isinstance(cond, dict):
                walk(cond)
        walk(node.get("child"))

    walk(root)


def _allocate_keys(names: list[str], reserved: set[str]) -> list[str]:
    keys: list[str] = []
    for idx, name in enumerate(names):
        base = _task_key_from_name(name, index=max(idx, 1))
        key = base
        suffix = 2
        while key in reserved:
            key = f"{base}{suffix}" if idx == 0 else f"{base}_{suffix}"
            suffix += 1
        reserved.add(key)
        keys.append(key)
    return keys


def compile_recruitment_from_guide(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
    start_task_name: str = "申请填报",
) -> dict:
    """
    P2a+P2b：指南 8 路 fan-out → 招聘复杂环节树；环节名取自各分支处理节点文字。
    """
    graph = GuideGraph.build(nodes)
    branch_plans = _extract_dept_branch_plans(graph)
    if len(branch_plans) < 4:
        raise ValueError(
            f"指南 DAG 未识别到足够的部门并行分支（got {len(branch_plans)}，需要 ≥4）"
        )

    ctx = _FlowCtx(proc_id=proc_id, settings=settings)
    root = ctx.start(task_name=start_task_name)
    dept_route = ctx.route(root["id"])
    root["child"] = dept_route
    dept_route["pid"] = root["id"]

    def single_approval(_ctx: _FlowCtx, pid: str, name: str, key: str) -> dict:
        return _ctx.usertask(pid, task_name=name, task_key=key)

    def chain_builder(_ctx: _FlowCtx, pid: str, names: list[str], keys: list[str]) -> dict:
        head = _ctx.usertask(pid, task_name=names[0], task_key=keys[0])
        cur = head
        for task_name, task_key in zip(names[1:], keys[1:], strict=False):
            nxt = _ctx.usertask(None, task_name=task_name, task_key=task_key)
            cur["child"] = nxt
            nxt["pid"] = cur["id"]
            cur = nxt
        return head

    conditions: list[dict] = []
    for plan in branch_plans:
        spec = plan.spec
        names = plan.task_names
        approval_keys = spec.get("approval_keys")
        if approval_keys:
            keys = list(approval_keys[: len(names)])
            while len(keys) < len(names):
                keys.append(_task_key_from_name(names[len(keys)], index=len(keys) + 1))
        else:
            keys = _allocate_keys(names, ctx.used_keys)
            if "approval_key" in spec:
                keys[0] = str(spec["approval_key"])

        if len(names) == 1:
            nm, ky = names[0], keys[0]

            def builder(c: _FlowCtx, p: str, name=nm, key=ky) -> dict:
                return single_approval(c, p, name, key)
        else:
            def builder(c: _FlowCtx, p: str, ns=names, ks=keys) -> dict:
                return chain_builder(c, p, ns, ks)

        conditions.append(
            _zpbm_branch(
                ctx,
                dept_route["id"],
                branch_name=str(spec["branch_name"]),
                branch_key=str(spec["branch_key"]),
                dept_label=str(spec["dept_label"]),
                dept_value=str(spec["dept_value"]),
                sort=plan.sort,
                child_builder=builder,
            )
        )

    dept_route["conditions"] = conditions

    merge_name = _find_merge_name(graph)
    merge_task = ctx.usertask(dept_route["id"], task_name=merge_name, task_key="zcbzrsp2")
    dept_route["child"] = merge_task
    merge_task["pid"] = dept_route["id"]

    tail = _build_tail_after_merge(
        ctx,
        merge_task["id"],
        cc_recipients=_extract_cc_recipients_from_graph(graph),
    )
    merge_task["child"] = tail
    tail["pid"] = merge_task["id"]

    _apply_task_name_overrides(root, _tail_task_overrides(graph))
    return root


def _link_tasks(parent: dict, child: dict) -> None:
    parent["child"] = child
    child["pid"] = parent["id"]


def _initiator_seq(graph: GuideGraph) -> int | None:
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        if node.node_type != "处理节点":
            continue
        text = (node.content or "").strip()
        if text in {"发起人", "发件人", "提交人"}:
            return node.seq
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        if node.node_type == "开始节点" and node.next_seq:
            return node.next_seq[0]
    return None


def _pick_main_next_seq(graph: GuideGraph, node: WorkflowNodeItem) -> int | None:
    if not node.next_seq:
        return None
    if len(node.next_seq) == 1:
        return node.next_seq[0]
    proc_candidates = [
        s
        for s in node.next_seq
        if (n := graph.nodes.get(s))
        and n.node_type == "处理节点"
        and not _is_guide_cc_node(n)
    ]
    if proc_candidates:
        return min(proc_candidates)
    return node.next_seq[0]


def _side_cc_nodes(graph: GuideGraph, from_seq: int, main_next: int | None) -> list[WorkflowNodeItem]:
    cc_nodes: list[WorkflowNodeItem] = []
    for node in graph.nodes.values():
        if node.prev_seq != [from_seq]:
            continue
        if main_next is not None and node.seq == main_next:
            continue
        if _is_guide_cc_node(node):
            cc_nodes.append(node)
    return sorted(cc_nodes, key=lambda n: n.seq)


def _is_side_cc_fanout(group: list[WorkflowNodeItem], graph: GuideGraph) -> bool:
    if len(group) < 2:
        return False
    cc_nodes = [n for n in group if _is_guide_cc_node(n)]
    proc_nodes = [n for n in group if n.node_type == "处理节点" and not _is_guide_cc_node(n)]
    return bool(cc_nodes) and bool(proc_nodes)


def _is_conditional_fanout(group: list[WorkflowNodeItem], graph: GuideGraph) -> bool:
    if len(group) < 2:
        return False
    parent_seq = group[0].prev_seq
    if len(parent_seq) != 1:
        return False
    parent = graph.nodes.get(parent_seq[0])
    return parent is not None and parent.node_type == "判断条件"


def _approval_parallel_fanout_groups(graph: GuideGraph) -> list[list[WorkflowNodeItem]]:
    """真实并行审批 fan-out（排除侧向抄送、菱形条件分支）。"""
    groups: list[list[WorkflowNodeItem]] = []
    for group in graph.fanout_groups():
        if _is_side_cc_fanout(group, graph):
            continue
        if _is_conditional_fanout(group, graph):
            continue
        if any(_is_guide_cc_node(n) for n in group):
            continue
        groups.append(group)
    return groups


def _is_amount_diamond(node: WorkflowNodeItem) -> bool:
    text = (node.content or "").strip()
    return "金额" in text


def _amount_option_for_branch_label(text: str) -> dict[str, str] | None:
    """从分支节点文字推断金额条件（支持 1000 / 1万 等写法）。"""
    raw = (text or "").strip()
    compact = re.sub(r"\s+", "", raw)
    if not compact:
        return None
    lt = bool(re.search(r"[≤<＜]|小于", compact))
    gt = bool(re.search(r"[＞>≥]|大于|等于", compact))
    if lt:
        label = raw.replace("金额", "").strip()[:30] or "小于1000"
        return {"label": label, "value": "0"}
    if gt:
        label = raw.replace("金额", "").strip()[:30] or "大于1000"
        return {"label": label, "value": "1"}
    return None


def _is_expense_type_diamond(node: WorkflowNodeItem) -> bool:
    return "费用类型" in ((node.content or "").strip())


def _expense_type_option_for_approver(content: str) -> dict[str, str] | None:
    from core.workflow.complex_flow_builder import FYLX_DAILY, FYLX_MAJOR

    role = strip_person_name(content or "")
    if "业务负责人" in role:
        return FYLX_DAILY
    if "总经理" in role:
        return FYLX_MAJOR
    return None


def _diamond_sibling_nodes(graph: GuideGraph, diamond: WorkflowNodeItem) -> list[WorkflowNodeItem]:
    return sorted(
        (graph.nodes[s] for s in diamond.next_seq if s in graph.nodes),
        key=lambda n: n.seq,
    )


def _is_terminal_side_cc_branch(
    diamond: WorkflowNodeItem,
    node: WorkflowNodeItem,
    siblings: list[WorkflowNodeItem],
) -> bool:
    """菱形下无后续、且其余分支仍向前延伸的多角色节点 → 侧向抄送，非 ROUTE 分支。"""
    if node.next_seq:
        return False
    if not (_is_guide_cc_node(node) or looks_like_multi_recipient_cc(node.content or "")):
        return False
    return any(
        s.seq != node.seq and (bool(s.next_seq) or s.node_type == "判断条件")
        for s in siblings
    )


def _reachable_from(graph: GuideGraph, head: WorkflowNodeItem) -> set[int]:
    seen: set[int] = set()
    queue = list(head.next_seq)
    while queue:
        seq = queue.pop(0)
        if seq in seen:
            continue
        seen.add(seq)
        node = graph.nodes.get(seq)
        if node:
            queue.extend(node.next_seq)
    return seen


def _find_merge_node(graph: GuideGraph, branch_heads: list[WorkflowNodeItem]) -> WorkflowNodeItem | None:
    if not branch_heads:
        return None
    # 含分支头自身：避免「A→B 汇聚、B→下游」时误把 B 的下游当作 merge（如印章用印：4→5 汇聚）
    reachables = [_reachable_from(graph, h) | {h.seq} for h in branch_heads]
    non_empty = [r for r in reachables if r]
    if len(non_empty) < 2:
        if len(non_empty) == 1:
            common = non_empty[0]
        else:
            return None
    else:
        common = set.intersection(*non_empty)
    if not common:
        return None
    head_seqs = {h.seq for h in branch_heads}
    # 优先菱形汇聚点（如印章所属板块），再取处理节点
    for seq in sorted(common):
        if seq in head_seqs:
            continue
        node = graph.nodes.get(seq)
        if node and node.node_type == "判断条件":
            return node
    for seq in sorted(common):
        if seq in head_seqs:
            continue
        node = graph.nodes.get(seq)
        if node and node.node_type == "处理节点" and not _is_guide_cc_node(node):
            return node
    return None


_LINE_LABEL_BRANCH_NAMES = frozenset({"其他", "龙馆", "龙池"})


def _is_line_label_node(node: WorkflowNodeItem) -> bool:
    """连线上的分支标签（非独立审批框），VLM 常误识别为处理节点。"""
    text = (node.content or "").strip()
    if text in _LINE_LABEL_BRANCH_NAMES:
        return True
    if node.node_type != "处理节点" or "\n" in text:
        return False
    if len(text) <= 6 and not any(k in text for k in ("总监", "经理", "会计", "出纳", "主管", "副", "内勤")):
        return True
    return False


def _subsidiary_family_diamond(diamond: WorkflowNodeItem) -> bool:
    return (diamond.content or "").strip() in {"子公司", "丝路驿站", "分销网站"}


def _branch_display_label(head: WorkflowNodeItem, diamond: WorkflowNodeItem) -> str:
    text = (head.content or "").strip()
    if _is_line_label_node(head):
        if text == "龙池":
            return "龙馆"
        return text[:30]
    if head.node_type == "判断条件":
        return (head.content or "")[:30]
    if "\n" in text:
        blob = text.replace("\n", " ")
        if "马业" in blob:
            return "马业部"
        if any(k in blob for k in ("料场", "马背", "大汉", "餐饮")):
            return "料场/马背/餐饮"
        return text.split("\n")[0].strip()[:30]
    return text[:30]


def _subsidiary_branch_option(
    diamond: WorkflowNodeItem,
    label: str,
    sort: int,
) -> dict[str, str]:
    from core.workflow.complex_flow_builder import (
        SLYZ_BRANCH_OPTIONS,
        ZGS_BRANCH_OPTIONS,
    )

    key = (diamond.content or "").strip()
    pools = {
        "子公司": ZGS_BRANCH_OPTIONS,
        "丝路驿站": SLYZ_BRANCH_OPTIONS,
    }
    pool = pools.get(key, [])
    for opt in pool:
        if opt["label"] == label:
            return dict(opt)
    if pool and sort < len(pool):
        return dict(pool[sort])
    return {"label": label[:30], "value": str(sort)}


def _find_silk_road_other_approver(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> WorkflowNodeItem | None:
    """丝路驿站「其他」连线分支：房务总监单节点（parse 常漏第三路）。"""
    return _find_role_approver_node(graph, "房务总监", after_seq=diamond.seq)


def _find_role_approver_node(
    graph: GuideGraph,
    role: str,
    *,
    after_seq: int = 0,
) -> WorkflowNodeItem | None:
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        if node.seq <= after_seq or node.node_type != "处理节点":
            continue
        text = (node.content or "").strip()
        if role in text and "\n" not in text and not _is_guide_cc_node(node):
            return node
    return None


def _looks_like_procurement_loan_dag(graph: GuideGraph) -> bool:
    texts = " ".join((n.content or "") for n in graph.nodes.values())
    if "采购" not in texts or "子公司" not in texts:
        return False
    return sum(1 for n in graph.nodes.values() if _is_amount_diamond(n)) >= 2


def _procurement_loan_amount_seqs(graph: GuideGraph) -> tuple[int | None, int | None]:
    """采购借款：子公司下第一处金额菱形、费用部门汇聚后第二处金额菱形。"""
    amounts = sorted(n.seq for n in graph.nodes.values() if _is_amount_diamond(n))
    if len(amounts) < 2:
        return None, None
    zgs = next(
        (n for n in graph.nodes.values() if (n.content or "").strip() == "子公司"),
        None,
    )
    if zgs is None:
        return amounts[0], amounts[-1]
    first = next((s for s in amounts if s > zgs.seq), amounts[0])
    second = amounts[-1] if amounts[-1] != first else amounts[1]
    return first, second


def _procurement_loan_dept_diamond_node(graph: GuideGraph) -> WorkflowNodeItem | None:
    return next(
        (n for n in graph.nodes.values() if (n.content or "").strip() == "部门名称"),
        None,
    )


def _procurement_loan_dept_merge_nodes(
    graph: GuideGraph,
) -> tuple[WorkflowNodeItem | None, WorkflowNodeItem | None, WorkflowNodeItem | None]:
    culture: WorkflowNodeItem | None = None
    hotel: WorkflowNodeItem | None = None
    director: WorkflowNodeItem | None = None
    for node in sorted(graph.nodes.values(), key=lambda n: n.seq):
        text = (node.content or "").strip()
        if "文旅文化财务副经理" in text:
            culture = node
        elif "文旅酒店财务副经理" in text:
            hotel = node
        elif (
            "财务总监" in text
            and "副经理" not in text
            and node.node_type in {"处理节点", "抄送节点"}
        ):
            director = node
    return culture, hotel, director


def _first_processing_successor(
    graph: GuideGraph,
    head: WorkflowNodeItem,
) -> WorkflowNodeItem | None:
    for seq in head.next_seq:
        node = graph.nodes.get(seq)
        if node and node.node_type == "处理节点" and not _is_guide_cc_node(node):
            return node
    return None


def _is_procurement_loan_dept_diamond(graph: GuideGraph, diamond: WorkflowNodeItem) -> bool:
    return _looks_like_procurement_loan_dag(graph) and (diamond.content or "").strip() == "部门名称"


def _is_procurement_loan_dept_silk_road_diamond(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> bool:
    if not _looks_like_procurement_loan_dag(graph):
        return False
    if (diamond.content or "").strip() != "丝路驿站":
        return False
    dept = _procurement_loan_dept_diamond_node(graph)
    return dept is not None and dept.seq < diamond.seq


def _procurement_loan_dept_branch_option(label: str, sort: int) -> dict[str, str]:
    from core.workflow.complex_flow_builder import BMMC_BRANCH_OPTIONS

    for opt in BMMC_BRANCH_OPTIONS:
        if opt["label"] == label:
            return dict(opt)
    if sort < len(BMMC_BRANCH_OPTIONS):
        return dict(BMMC_BRANCH_OPTIONS[sort])
    return {"label": label[:30], "value": str(sort)}


def _procurement_loan_dept_silk_option(label: str, sort: int) -> dict[str, str]:
    from core.workflow.complex_flow_builder import BMMC_SLYZ_BRANCH_OPTIONS

    for opt in BMMC_SLYZ_BRANCH_OPTIONS:
        if opt["label"] == label:
            return dict(opt)
    if sort < len(BMMC_SLYZ_BRANCH_OPTIONS):
        return dict(BMMC_SLYZ_BRANCH_OPTIONS[sort])
    return {"label": label[:30], "value": str(sort)}


def _procurement_loan_dept_branch_specs(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> list[tuple[WorkflowNodeItem, str, dict[str, str]]]:
    siblings = _diamond_sibling_nodes(graph, diamond)
    specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
    sort = 0
    for head in siblings:
        text = (head.content or "").strip()
        if text == "其他" and head.next_seq:
            nxt = graph.nodes.get(head.next_seq[0])
            if nxt and (nxt.content or "").strip() == "其他":
                continue
        if "\n" in text and any(
            k in text for k in ("物业", "野马时光", "霍尔果斯", "旅行社")
        ):
            acct = _first_processing_successor(graph, head)
            if acct:
                specs.append(
                    (
                        acct,
                        "dept_branch_culture",
                        _procurement_loan_dept_branch_option("物业公司", sort),
                    )
                )
                sort += 1
            continue
        if text == "其他":
            acct = _first_processing_successor(graph, head)
            if acct:
                specs.append(
                    (
                        acct,
                        "dept_branch_culture",
                        _procurement_loan_dept_branch_option("其他", sort),
                    )
                )
                sort += 1
            continue
        if "工坊" in text:
            acct = _first_processing_successor(graph, head)
            if acct:
                specs.append(
                    (
                        acct,
                        "dept_branch_culture",
                        _procurement_loan_dept_branch_option("工坊文化创意", sort),
                    )
                )
                sort += 1
            continue
        if text == "丝路驿站" and head.node_type == "判断条件":
            specs.append(
                (
                    head,
                    "nested_diamond",
                    _procurement_loan_dept_branch_option("丝路驿站", sort),
                )
            )
            sort += 1
            continue
        if "餐饮管理" in text:
            acct = _first_processing_successor(graph, head)
            if acct:
                specs.append(
                    (
                        acct,
                        "dept_branch_hotel",
                        _procurement_loan_dept_branch_option("餐饮管理公司", sort),
                    )
                )
                sort += 1
    return specs if len(specs) >= 2 else []


def _procurement_loan_dept_silk_road_specs(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> list[tuple[WorkflowNodeItem, str, dict[str, str]]]:
    siblings = _diamond_sibling_nodes(graph, diamond)
    specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
    sort = 0
    for head in siblings:
        text = (head.content or "").strip()
        if head.node_type == "处理节点" and "酒店主管会计" in text:
            specs.append(
                (
                    head,
                    "dept_branch_hotel",
                    _procurement_loan_dept_silk_option("其他", sort),
                )
            )
            sort += 1
            continue
        if "\n" in text and any(k in text for k in ("料场", "马背", "大汉", "早餐")):
            acct = _find_role_approver_node(graph, "餐饮主管会计", after_seq=diamond.seq)
            if acct is None:
                acct = head
            specs.append(
                (
                    acct,
                    "dept_branch_hotel",
                    _procurement_loan_dept_silk_option("料场/马背/餐饮", sort),
                )
            )
            sort += 1
    return specs if len(specs) >= 2 else []


def _attach_dept_branch_chain(
    ctx: _FlowCtx,
    graph: GuideGraph,
    branch: dict,
    head: WorkflowNodeItem,
    *,
    hotel: bool,
) -> None:
    culture_deputy, hotel_deputy, _ = _procurement_loan_dept_merge_nodes(graph)
    deputy = hotel_deputy if hotel else culture_deputy
    content = (head.content or "").strip()
    if "\n" in content and hotel and "餐饮主管会计" not in content:
        content = "餐饮主管会计"
    acct_ut = ctx.usertask_from_guide(branch["id"], content, index=head.seq)
    branch["child"] = acct_ut
    acct_ut["pid"] = branch["id"]
    if deputy is not None:
        dep_ut = ctx.usertask_from_guide(
            acct_ut["id"],
            deputy.content or "",
            index=deputy.seq,
        )
        acct_ut["child"] = dep_ut
        dep_ut["pid"] = acct_ut["id"]


def _is_procurement_loan_zgs_diamond(graph: GuideGraph, diamond: WorkflowNodeItem) -> bool:
    return _looks_like_procurement_loan_dag(graph) and (diamond.content or "").strip() == "子公司"


def _is_procurement_loan_silk_road_diamond(graph: GuideGraph, diamond: WorkflowNodeItem) -> bool:
    if not _looks_like_procurement_loan_dag(graph):
        return False
    if (diamond.content or "").strip() != "丝路驿站":
        return False
    zgs = next(
        (n for n in graph.nodes.values() if (n.content or "").strip() == "子公司"),
        None,
    )
    dept = _procurement_loan_dept_diamond_node(graph)
    upper = dept.seq if dept is not None else ((zgs.seq + 15) if zgs else diamond.seq)
    return zgs is not None and zgs.seq < diamond.seq < upper


def _procurement_loan_zgs_branch_specs(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> list[tuple[WorkflowNodeItem, str, dict[str, str]]]:
    siblings = _diamond_sibling_nodes(graph, diamond)
    specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
    sort = 0
    for head in siblings:
        if _is_terminal_side_cc_branch(diamond, head, siblings):
            specs.append(
                (
                    head,
                    "passthrough",
                    _subsidiary_branch_option(diamond, "其他", sort),
                )
            )
            sort += 1
            continue
        text = (head.content or "").strip()
        if "\n" in text and "马业" in text:
            label = _branch_display_label(head, diamond)
            specs.append((head, "passthrough", _subsidiary_branch_option(diamond, label, sort)))
            sort += 1
            continue
        if head.node_type == "判断条件" and text == "丝路驿站":
            specs.append(
                (head, "nested_diamond", _subsidiary_branch_option(diamond, "丝路驿站", sort))
            )
            sort += 1
    return specs if len(specs) >= 2 else []


def _procurement_loan_silk_road_branch_specs(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> list[tuple[WorkflowNodeItem, str, dict[str, str]]]:
    siblings = _diamond_sibling_nodes(graph, diamond)
    specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
    sort = 0
    for head in siblings:
        if _is_line_label_node(head):
            label = _branch_display_label(head, diamond)
            specs.append((head, "passthrough", _subsidiary_branch_option(diamond, label, sort)))
            sort += 1
            continue
        text = (head.content or "").strip()
        if "\n" in text and any(k in text for k in ("料场", "马背", "大汉", "餐饮")):
            approver = _find_role_approver_node(graph, "餐饮副总监", after_seq=diamond.seq)
            if approver:
                specs.append(
                    (
                        approver,
                        "approval_once",
                        _subsidiary_branch_option(diamond, "料场/马背/餐饮", sort),
                    )
                )
                sort += 1
    has_other = any(opt.get("label") == "其他" for _, _, opt in specs)
    if not has_other:
        approver = _find_role_approver_node(graph, "房务总监", after_seq=diamond.seq)
        if approver:
            insert_at = 1 if specs else 0
            specs.insert(
                insert_at,
                (
                    approver,
                    "approval_once",
                    _subsidiary_branch_option(diamond, "其他", insert_at),
                ),
            )
    return specs if len(specs) >= 2 else []


def _subsidiary_branch_specs(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> list[tuple[WorkflowNodeItem, str, dict[str, str]]]:
    if _is_procurement_loan_zgs_diamond(graph, diamond):
        pl = _procurement_loan_zgs_branch_specs(graph, diamond)
        if pl:
            return pl
    if _is_procurement_loan_dept_silk_road_diamond(graph, diamond):
        pl = _procurement_loan_dept_silk_road_specs(graph, diamond)
        if pl:
            return pl
    if _is_procurement_loan_silk_road_diamond(graph, diamond):
        pl = _procurement_loan_silk_road_branch_specs(graph, diamond)
        if pl:
            return pl

    siblings = _diamond_sibling_nodes(graph, diamond)
    specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
    sort = 0

    for head in siblings:
        if _is_terminal_side_cc_branch(diamond, head, siblings):
            specs.append(
                (
                    head,
                    "passthrough_cc",
                    _subsidiary_branch_option(diamond, "其他", sort),
                )
            )
            sort += 1
            continue
        if _is_line_label_node(head):
            label = _branch_display_label(head, diamond)
            specs.append((head, "passthrough", _subsidiary_branch_option(diamond, label, sort)))
            sort += 1
            continue
        if head.node_type == "判断条件":
            label = _branch_display_label(head, diamond)
            specs.append((head, "nested_diamond", _subsidiary_branch_option(diamond, label, sort)))
            sort += 1
            continue
        if head.node_type == "处理节点" and not _is_guide_cc_node(head):
            label = _branch_display_label(head, diamond)
            specs.append((head, "approval", _subsidiary_branch_option(diamond, label, sort)))
            sort += 1

    if (diamond.content or "").strip() == "丝路驿站":
        has_other = any(opt.get("label") == "其他" for _, _, opt in specs)
        if not has_other:
            approver = _find_silk_road_other_approver(graph, diamond)
            if approver:
                insert_at = 1 if specs else 0
                specs.insert(
                    insert_at,
                    (
                        approver,
                        "approval_once",
                        _subsidiary_branch_option(diamond, "其他", insert_at),
                    ),
                )

    if len(specs) >= 2:
        return specs
    return []


def _diamond_branch_specs(
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
) -> list[tuple[WorkflowNodeItem, str, dict[str, str]]]:
    """菱形分支 → (节点, kind=approval|cc, 条件选项)。"""
    siblings = _diamond_sibling_nodes(graph, diamond)
    if len(siblings) < 2:
        return []

    if _is_procurement_loan_dept_diamond(graph, diamond):
        pl = _procurement_loan_dept_branch_specs(graph, diamond)
        if pl:
            return pl

    if _subsidiary_family_diamond(diamond):
        sub = _subsidiary_branch_specs(graph, diamond)
        if sub:
            return sub

    if _is_amount_diamond(diamond):
        from core.workflow.complex_flow_builder import JEFD_GTE_10K, JEFD_LT_10K

        active = [s for s in siblings if not _is_terminal_side_cc_branch(diamond, s, siblings)]
        if len(active) < 2:
            active = list(siblings)
        specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
        for sort, child in enumerate(active):
            opt = _amount_option_for_branch_label(child.content or "")
            if opt is None:
                if child.node_type == "判断条件":
                    text = (child.content or "").strip()
                    opt = JEFD_LT_10K if "小于" in text else JEFD_GTE_10K
                else:
                    opt = JEFD_LT_10K if sort == 0 else JEFD_GTE_10K
            if child.node_type == "判断条件" and not _is_amount_diamond(child):
                specs.append((child, "nested_diamond", opt))
            else:
                specs.append((child, "amount_followup", opt))
        if len(specs) >= 2:
            return specs
        return []

    if _is_expense_type_diamond(diamond):
        from core.workflow.complex_flow_builder import FYLX_DAILY, FYLX_MAJOR

        proc_siblings = [n for n in siblings if n.node_type == "处理节点" and not _is_guide_cc_node(n)]
        specs = []
        for child in proc_siblings:
            opt = _expense_type_option_for_approver(child.content or "")
            if opt:
                specs.append((child, "approval", opt))
        if len(specs) >= 2:
            return specs
        if len(proc_siblings) >= 2:
            return [
                (proc_siblings[0], "approval", FYLX_DAILY),
                (proc_siblings[1], "approval", FYLX_MAJOR),
            ]
        return []

    active = [s for s in siblings if not _is_terminal_side_cc_branch(diamond, s, siblings)]
    if len(active) >= 2:
        proc_siblings = [n for n in active if n.node_type == "处理节点" and not _is_guide_cc_node(n)]
        nested_diamonds = [n for n in active if n.node_type == "判断条件"]
        specs: list[tuple[WorkflowNodeItem, str, dict[str, str]]] = []
        idx = 0
        for head in proc_siblings:
            label = (head.content or f"分支{idx + 1}").replace("\n", " ")[:30]
            others = [h for h in proc_siblings if h.seq != head.seq]
            is_passthrough = bool(others) and all(
                head.seq in (_reachable_from(graph, o) | {o.seq}) for o in others
            )
            if is_passthrough:
                specs.append((head, "passthrough", {"label": label, "value": str(idx)}))
            else:
                specs.append((head, "approval", {"label": label, "value": str(idx)}))
            idx += 1
        for head in nested_diamonds:
            label = (head.content or f"分支{idx + 1}")[:30]
            specs.append((head, "nested_diamond", {"label": label, "value": str(idx)}))
            idx += 1
        if len(specs) >= 2:
            return specs

    terminal_cc = [s for s in siblings if _is_terminal_side_cc_branch(diamond, s, siblings)]
    nested_continue = [
        n for n in siblings if n.node_type == "判断条件" and n.next_seq and n not in terminal_cc
    ]
    if terminal_cc and nested_continue:
        specs = []
        for i, cc in enumerate(terminal_cc):
            specs.append((cc, "cc", {"label": "抄送", "value": str(i)}))
        for j, nd in enumerate(nested_continue):
            specs.append(
                (
                    nd,
                    "nested_diamond",
                    {"label": (nd.content or f"分支{j + 1}")[:30], "value": str(len(terminal_cc) + j)},
                )
            )
        if len(specs) >= 2:
            return specs

    cc_child = next((n for n in siblings if _is_guide_cc_node(n)), None)
    ut_child = next((n for n in siblings if n.node_type == "处理节点" and not _is_guide_cc_node(n)), None)
    if cc_child and ut_child and not _is_terminal_side_cc_branch(diamond, cc_child, siblings):
        return [
            (cc_child, "cc", {"label": "抄送", "value": "0"}),
            (ut_child, "approval", {"label": (ut_child.content or "审批")[:30], "value": "1"}),
        ]
    return []


def _conditional_fanout_groups(graph: GuideGraph) -> list[tuple[WorkflowNodeItem, list[WorkflowNodeItem]]]:
    result: list[tuple[WorkflowNodeItem, list[WorkflowNodeItem]]] = []
    for group in graph.fanout_groups():
        if not _is_conditional_fanout(group, graph):
            continue
        parent_seq = group[0].prev_seq[0]
        parent = graph.nodes[parent_seq]
        specs = _diamond_branch_specs(graph, parent)
        if len(specs) >= 2:
            result.append((parent, [head for head, _, _ in specs]))
    return result


def _post_branch_cc_nodes(
    graph: GuideGraph,
    branch_heads: list[WorkflowNodeItem],
    merge: WorkflowNodeItem | None,
) -> list[WorkflowNodeItem]:
    branch_prev = {h.seq for h in branch_heads}
    cc_nodes: list[WorkflowNodeItem] = []
    for node in graph.nodes.values():
        if not _is_guide_cc_node(node):
            continue
        prev = set(node.prev_seq)
        if merge is not None and merge.seq in prev:
            cc_nodes.append(node)
            continue
        if prev == branch_prev:
            cc_nodes.append(node)
    return sorted(cc_nodes, key=lambda n: n.seq)


def _append_cc_subtree(ctx: _FlowCtx, tail: dict, content: str) -> dict:
    cc_root = _build_cc_chain(ctx, split_cc_recipients(content or ""))
    if not cc_root:
        return tail
    _link_tasks(tail, cc_root)
    node = cc_root
    while node.get("child"):
        node = node["child"]
    return node


def _branch_field_prop(condition_label: str) -> str:
    from core.workflow.shenbi_builder import label_to_prop

    label = (condition_label or "分支条件").strip()
    mapping = {
        "费用类型": "fylx",
        "出纳类型": "cnlx",
        "金额": "jefd",
        "金额分段": "jefd",
        "子公司": "zgs",
        "部门名称": "bmmc",
        "分销网站": "fxwz",
        "丝路驿站": "slyz",
    }
    if label in mapping:
        return mapping[label]
    return label_to_prop(label, set())


def _diamond_field_meta(diamond: WorkflowNodeItem) -> tuple[str, str]:
    """返回 (field_prop, field_name)。"""
    from core.workflow.complex_flow_builder import JEFD_FIELD

    label = (diamond.content or "分支条件").strip()
    if _is_amount_diamond(diamond):
        return JEFD_FIELD, "金额分段"
    return _branch_field_prop(label), label[:20]


def _build_branch_chain_until(
    ctx: _FlowCtx,
    graph: GuideGraph,
    head: WorkflowNodeItem,
    stop_seq: int | None,
) -> dict | None:
    """从分支头沿主链编译审批环节，至汇聚点或嵌套菱形前停止。"""
    seq: int | None = head.seq
    if head.node_type == "处理节点" and "\n" in (head.content or "") and head.next_seq:
        seq = head.next_seq[0]
    root: dict | None = None
    tail: dict | None = None
    while seq is not None and seq != stop_seq:
        node = graph.nodes.get(seq)
        if node is None:
            break
        if node.node_type == "判断条件":
            break
        if _is_guide_cc_node(node):
            seq = _pick_main_next_seq(graph, node)
            continue
        if node.node_type == "处理节点":
            ut = ctx.usertask_from_guide(tail["id"] if tail else None, node.content or "", index=node.seq)
            if tail:
                _link_tasks(tail, ut)
            else:
                root = ut
            tail = ut
        seq = _pick_main_next_seq(graph, node)
    return root


def _attach_branch_child(
    ctx: _FlowCtx,
    graph: GuideGraph,
    branch: dict,
    head: WorkflowNodeItem,
    kind: str,
    cond_opt: dict[str, str],
    *,
    merge_seq: int | None,
    handled_diamonds: set[int],
    pending_side_cc: list[WorkflowNodeItem],
) -> None:
    if kind == "cc":
        cc_root = _build_cc_chain(ctx, split_cc_recipients(head.content or ""))
        if cc_root:
            branch["child"] = cc_root
            cc_root["pid"] = branch["id"]
    elif kind == "amount_followup":
        from core.workflow.expense_flow_compiler import _amount_branch_followup

        is_lt = str(cond_opt.get("value")) == "0"
        follow = _amount_branch_followup(ctx, head, lt_10k=is_lt)
        if follow:
            branch["child"] = follow
            follow["pid"] = branch["id"]
    elif kind == "nested_diamond":
        if head.seq in handled_diamonds:
            return
        sub_route = ctx.route(branch["id"])
        branch["child"] = sub_route
        sub_route["pid"] = branch["id"]
        _compile_diamond_route(ctx, graph, head, sub_route, handled_diamonds, pending_side_cc)
    elif kind == "approval":
        chain = _build_branch_chain_until(ctx, graph, head, merge_seq)
        if chain:
            branch["child"] = chain
            chain["pid"] = branch["id"]
    elif kind == "approval_once":
        ut = ctx.usertask_from_guide(branch["id"], head.content or "", index=head.seq)
        branch["child"] = ut
        ut["pid"] = branch["id"]
    elif kind in {"dept_branch_culture", "dept_branch_hotel"}:
        _attach_dept_branch_chain(
            ctx,
            graph,
            branch,
            head,
            hotel=kind == "dept_branch_hotel",
        )
    elif kind == "passthrough":
        return
    elif kind == "passthrough_jump":
        jump_seq = cond_opt.get("_jump_seq")
        if isinstance(jump_seq, str) and jump_seq.isdigit():
            jump_seq = int(jump_seq)
        if isinstance(jump_seq, int) and jump_seq in graph.nodes:
            sub_route = ctx.route(branch["id"])
            branch["child"] = sub_route
            sub_route["pid"] = branch["id"]
            jump_node = graph.nodes[jump_seq]
            _compile_diamond_route(
                ctx, graph, jump_node, sub_route, handled_diamonds, pending_side_cc
            )
    elif kind == "passthrough_cc":
        cc_root = _build_cc_chain(ctx, split_cc_recipients(head.content or ""))
        if cc_root:
            branch["child"] = cc_root
            cc_root["pid"] = branch["id"]


def _compile_diamond_route(
    ctx: _FlowCtx,
    graph: GuideGraph,
    diamond: WorkflowNodeItem,
    tail: dict,
    handled_diamonds: set[int],
    pending_side_cc: list[WorkflowNodeItem],
    *,
    pl_first_amt_on_branch: bool = False,
) -> tuple[dict, int | None]:
    siblings = _diamond_sibling_nodes(graph, diamond)
    if _is_amount_diamond(diamond):
        for s in siblings:
            if _is_terminal_side_cc_branch(diamond, s, siblings):
                tail = _append_cc_subtree(ctx, tail, s.content or "")
    elif not _subsidiary_family_diamond(diamond):
        for s in siblings:
            if _is_terminal_side_cc_branch(diamond, s, siblings):
                pending_side_cc.append(s)

    branch_specs = _diamond_branch_specs(graph, diamond)
    if len(branch_specs) < 2:
        siblings = _diamond_sibling_nodes(graph, diamond)
        for s in siblings:
            if _is_terminal_side_cc_branch(diamond, s, siblings):
                tail = _append_cc_subtree(ctx, tail, s.content or "")
        nested = [s for s in siblings if s.node_type == "判断条件" and s.next_seq]
        if len(nested) == 1:
            handled_diamonds.add(diamond.seq)
            return _compile_diamond_route(
                ctx, graph, nested[0], tail, handled_diamonds, pending_side_cc
            )
        nxt = _pick_main_next_seq(graph, diamond)
        return tail, nxt

    handled_diamonds.add(diamond.seq)
    branch_heads = [
        head
        for head, kind, _ in branch_specs
        if kind
        not in {
            "passthrough",
            "passthrough_cc",
            "passthrough_jump",
            "dept_branch_culture",
            "dept_branch_hotel",
        }
    ]
    if not branch_heads:
        branch_heads = [head for head, _, _ in branch_specs]

    is_pl_zgs = _is_procurement_loan_zgs_diamond(graph, diamond)
    is_pl_silk = _is_procurement_loan_silk_road_diamond(graph, diamond)
    is_pl_dept = _is_procurement_loan_dept_diamond(graph, diamond)
    is_pl_dept_silk = _is_procurement_loan_dept_silk_road_diamond(graph, diamond)
    first_amt, second_amt = _procurement_loan_amount_seqs(graph)

    passthrough_merge = next(
        (head for head, kind, _ in branch_specs if kind == "passthrough"),
        None,
    )
    if passthrough_merge is not None:
        merge = passthrough_merge
        merge_seq = merge.seq
    elif is_pl_dept or is_pl_dept_silk:
        merge = None
        merge_seq = None
    elif is_pl_zgs or is_pl_silk:
        merge = graph.nodes.get(first_amt) if first_amt is not None else None
        merge_seq = first_amt
    else:
        merge = _find_merge_node(graph, branch_heads)
        merge_seq = merge.seq if merge is not None else None

    if tail.get("type") == "ROUTE" and not tail.get("conditions"):
        route = tail
    else:
        route = ctx.route(tail["id"])
        _link_tasks(tail, route)
    field_prop, field_name = _diamond_field_meta(diamond)
    branch_nodes: list[dict] = []
    for sort, (head, kind, cond_opt) in enumerate(branch_specs):
        branch_label = str(cond_opt.get("label") or head.content or f"分支{sort + 1}")[:30]
        branch_key = (
            f"{field_prop}{sort}"
            if field_prop in {"zgs", "slyz", "fxwz", "fylx", "jefd", "bmmc"}
            else f"br{sort + 1}"
        )
        branch = ctx.branch(
            route["id"],
            branch_name=branch_label,
            branch_key=branch_key,
            field_prop=field_prop,
            field_name=field_name,
            condition_options=[cond_opt],
            sort=sort,
        )
        _attach_branch_child(
            ctx,
            graph,
            branch,
            head,
            kind,
            cond_opt,
            merge_seq=merge_seq,
            handled_diamonds=handled_diamonds,
            pending_side_cc=pending_side_cc,
        )
        branch_nodes.append(branch)
    route["conditions"] = branch_nodes

    if (
        _looks_like_procurement_loan_dag(graph)
        and pl_first_amt_on_branch
        and first_amt is not None
        and diamond.seq == first_amt
    ):
        return route, None

    if is_pl_dept_silk:
        return route, None

    if is_pl_dept:
        _, _, director = _procurement_loan_dept_merge_nodes(graph)
        if director is not None:
            if _is_guide_cc_node(director):
                cc_root = _build_cc_chain(ctx, split_cc_recipients(director.content or ""))
                if cc_root:
                    route["child"] = cc_root
                    cc_root["pid"] = route["id"]
                    tail = cc_root
                    while tail.get("child"):
                        tail = tail["child"]
                    return tail, None
            else:
                dir_ut = ctx.usertask_from_guide(
                    route["id"], director.content or "", index=director.seq
                )
                route["child"] = dir_ut
                dir_ut["pid"] = route["id"]
                return dir_ut, None
        return route, None

    if is_pl_silk:
        if first_amt is not None and first_amt in graph.nodes:
            _compile_diamond_route(
                ctx,
                graph,
                graph.nodes[first_amt],
                route,
                handled_diamonds,
                pending_side_cc,
                pl_first_amt_on_branch=True,
            )
        return route, None

    if is_pl_zgs and second_amt is not None and second_amt in graph.nodes:
        for branch in branch_nodes:
            if branch.get("taskName") == "马业部" and first_amt is not None and first_amt in graph.nodes:
                _compile_diamond_route(
                    ctx,
                    graph,
                    graph.nodes[first_amt],
                    branch,
                    handled_diamonds,
                    pending_side_cc,
                    pl_first_amt_on_branch=True,
                )
        return _compile_diamond_route(
            ctx,
            graph,
            graph.nodes[second_amt],
            route,
            handled_diamonds,
            pending_side_cc,
        )

    if (
        _looks_like_procurement_loan_dag(graph)
        and second_amt is not None
        and diamond.seq == second_amt
    ):
        dept = _procurement_loan_dept_diamond_node(graph)
        if dept is not None:
            return _compile_diamond_route(
                ctx,
                graph,
                dept,
                route,
                handled_diamonds,
                pending_side_cc,
            )
        return route, None

    if merge is not None:
        if merge.node_type == "判断条件":
            return _compile_diamond_route(
                ctx, graph, merge, route, handled_diamonds, pending_side_cc
            )
        merge_ut = ctx.usertask_from_guide(route["id"], merge.content or "", index=merge.seq)
        route["child"] = merge_ut
        merge_ut["pid"] = route["id"]
        new_tail = merge_ut
        for cc in _post_branch_cc_nodes(graph, branch_heads, merge):
            new_tail = _append_cc_subtree(ctx, new_tail, cc.content or "")
        for cc in _side_cc_nodes(graph, merge.seq, _pick_main_next_seq(graph, merge)):
            new_tail = _append_cc_subtree(ctx, new_tail, cc.content or "")
        return new_tail, _pick_main_next_seq(graph, merge)

    siblings = _diamond_sibling_nodes(graph, diamond)
    if branch_heads and all(not h.next_seq for h in branch_heads):
        return route, None

    continuing = [
        s
        for s in siblings
        if s.next_seq
        and s.seq not in handled_diamonds
        and not _is_terminal_side_cc_branch(diamond, s, siblings)
    ]
    if continuing:
        main = min(continuing, key=lambda n: n.seq)
        if main.node_type == "判断条件":
            return _compile_diamond_route(
                ctx, graph, main, route, handled_diamonds, pending_side_cc
            )
        return route, main.seq

    return route, None


def should_compile_serial_guide_dag(graph: GuideGraph) -> bool:
    """含侧向抄送或菱形条件分支、且非集团费用/招聘复杂 DAG 时走 serial_dag。"""
    from core.workflow.expense_flow_compiler import looks_like_expense_reimbursement_dag

    if looks_like_expense_reimbursement_dag(graph):
        return False
    if len(_extract_dept_branch_plans(graph)) >= 4:
        return False
    if _approval_parallel_fanout_groups(graph):
        return False
    for group in graph.fanout_groups():
        if _is_side_cc_fanout(group, graph) or _is_conditional_fanout(group, graph):
            return True
    return False


def compile_serial_guide_dag(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
    start_task_name: str = "申请填报",
) -> dict | None:
    """串行主链 + 侧向抄送 + 单菱形条件汇聚（子公司费用报销等）。"""
    graph = GuideGraph.build(nodes)
    if not should_compile_serial_guide_dag(graph):
        return None

    ctx = _FlowCtx(proc_id=proc_id, settings=settings)
    root = ctx.start(task_name=start_task_name)
    tail = root
    handled_diamonds: set[int] = set()
    pending_side_cc: list[WorkflowNodeItem] = []

    seq = _initiator_seq(graph)
    while seq is not None:
        node = graph.nodes.get(seq)
        if node is None:
            break
        if node.node_type in {"开始节点", "结束节点"}:
            seq = _pick_main_next_seq(graph, node)
            continue

        if node.node_type == "判断条件":
            if node.seq in handled_diamonds:
                seq = _pick_main_next_seq(graph, node)
                continue
            tail, seq = _compile_diamond_route(
                ctx, graph, node, tail, handled_diamonds, pending_side_cc
            )
            continue

        if _is_guide_cc_node(node):
            tail = _append_cc_subtree(ctx, tail, node.content or "")
            seq = _pick_main_next_seq(graph, node)
            continue

        if node.node_type == "处理节点":
            text = (node.content or "").strip()
            if _looks_like_procurement_loan_dag(graph) and "出纳" in text:
                seq = _pick_main_next_seq(graph, node)
                continue
            if text in {"发起人", "发件人", "提交人"}:
                main_next = _pick_main_next_seq(graph, node)
                pending_side_cc = _side_cc_nodes(graph, node.seq, main_next)
                seq = main_next
                continue
            main_next = _pick_main_next_seq(graph, node)
            ut = ctx.usertask_from_guide(tail["id"], text, index=node.seq)
            _link_tasks(tail, ut)
            tail = ut
            for cc in pending_side_cc:
                tail = _append_cc_subtree(ctx, tail, cc.content or "")
            pending_side_cc = []
            for cc in _side_cc_nodes(graph, node.seq, main_next):
                tail = _append_cc_subtree(ctx, tail, cc.content or "")
            seq = main_next
            continue

        seq = _pick_main_next_seq(graph, node)

    return root


def compile_linear_from_guide(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
) -> dict | None:
    from core.workflow.shenbi_builder import _build_linear_task_tree_from_steps

    steps = guide_nodes_to_semantic_steps(nodes)
    if not steps:
        return None
    return _build_linear_task_tree_from_steps(steps, proc_id=proc_id, settings=settings)


def compile_dag_parallel_merge(
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
    branch_field: str = "branchField",
    branch_field_label: str = "分支字段",
) -> dict | None:
    graph = GuideGraph.build(nodes)
    groups = graph.fanout_groups()
    if not groups:
        return None
    group = max(groups, key=len)
    if len(group) < 2:
        return None

    ctx = _FlowCtx(proc_id=proc_id, settings=settings)
    root = ctx.start(task_name="申请填报")
    route = ctx.route(root["id"])
    root["child"] = route
    route["pid"] = root["id"]

    conditions: list[dict] = []
    for sort, node in enumerate(group):
        text = (node.content or "").strip() or f"分支{sort + 1}"
        branch_key = f"branch_{sort + 1}"
        branch = ctx.branch(
            route["id"],
            branch_name=text[:30],
            branch_key=branch_key,
            field_prop=branch_field,
            field_name=branch_field_label,
            condition_options=[{"label": text[:30], "value": str(sort)}],
            sort=sort,
        )
        ut = ctx.usertask_from_guide(branch["id"], text, index=node.seq)
        branch["child"] = ut
        ut["pid"] = branch["id"]
        conditions.append(branch)

    route["conditions"] = conditions
    merge = ctx.usertask(route["id"], task_name="汇聚审批", task_key="merge")
    route["child"] = merge
    merge["pid"] = route["id"]
    return root


def compile_task_tree_from_guide(
    workflow_name: str,
    nodes: list[WorkflowNodeItem],
    *,
    proc_id: str,
    settings: Settings,
) -> tuple[dict | None, str]:
    """P2c 统一入口：按指南 DAG 拓扑自动选型编译器。返回 (环节树, task_tree_source)。"""
    del workflow_name  # 编译策略由 DAG 决定，不依赖 catalog
    if not nodes:
        return None, "empty"

    graph = GuideGraph.build(nodes)
    if len(_extract_dept_branch_plans(graph)) >= 4:
        tree = compile_recruitment_from_guide(
            nodes,
            proc_id=proc_id,
            settings=settings,
            start_task_name="申请填报",
        )
        return tree, "guide_dag_compiler:recruitment"

    from core.workflow.expense_flow_compiler import (
        compile_expense_from_guide,
        looks_like_expense_reimbursement_dag,
    )

    if looks_like_expense_reimbursement_dag(graph):
        tree = compile_expense_from_guide(
            nodes,
            proc_id=proc_id,
            settings=settings,
            start_task_name="申请填报",
        )
        return tree, "guide_dag_compiler:expense"

    from core.workflow.foreign_trade_expense_compiler import (
        compile_foreign_trade_expense_from_guide,
        looks_like_foreign_trade_expense_dag,
    )

    if looks_like_foreign_trade_expense_dag(graph):
        tree = compile_foreign_trade_expense_from_guide(
            nodes,
            proc_id=proc_id,
            settings=settings,
            start_task_name="申请填报",
        )
        return tree, "guide_dag_compiler:foreign_trade_expense"

    if should_compile_serial_guide_dag(graph):
        tree = compile_serial_guide_dag(
            nodes,
            proc_id=proc_id,
            settings=settings,
            start_task_name="申请填报",
        )
        if tree is not None:
            return tree, "guide_dag_compiler:serial_dag"

    if _approval_parallel_fanout_groups(graph):
        generic = compile_dag_parallel_merge(nodes, proc_id=proc_id, settings=settings)
        if generic is not None:
            return generic, "guide_dag_compiler:parallel_merge"

    linear = compile_linear_from_guide(nodes, proc_id=proc_id, settings=settings)
    return linear, "guide_dag_compiler:linear"


def recruitment_branch_fields_bound(tree: dict) -> bool:
    has_zpbm = has_zwjb = False
    for task in iter_all_tasks(tree):
        if task.get("type") != "BRANCHTASK":
            continue
        props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        for cond in props.get("branchConditionList") or []:
            prop = str(cond.get("fieldProp") or "")
            if prop == ZPBM_FIELD:
                has_zpbm = True
            if prop == ZWJB_FIELD:
                has_zwjb = True
    return has_zpbm and has_zwjb


def compile_semantic_summary(nodes: list[WorkflowNodeItem]) -> list[str]:
    from core.workflow.flow_semantics import semantic_steps_summary

    return semantic_steps_summary(guide_nodes_to_semantic_steps(nodes))
