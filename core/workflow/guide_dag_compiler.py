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
    _build_tail_after_merge,
    _zpbm_branch,
    count_task_types,
    iter_all_tasks,
)
from core.workflow.flow_semantics import guide_nodes_to_semantic_steps, split_cc_recipients
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
_PERSON_NAME = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
_ROLE_MARKERS = frozenset(
    "部办司处科组队委办经理总监主任主管部长分管会计专员审批总经理总裁董事"
)


def _strip_person_name(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    parts = text.rsplit(maxsplit=1)
    if len(parts) != 2:
        return text
    role, tail = parts
    if not role or not tail or not _PERSON_NAME.fullmatch(tail):
        return text
    if any(m in tail for m in _ROLE_MARKERS):
        return text
    return role


def _normalize_role_title(text: str) -> str:
    return _strip_person_name(text).strip()


def _task_name_from_content(text: str) -> str:
    text = _normalize_role_title(text)
    if not text:
        return "审批"
    if text.endswith("审批"):
        return text[:60]
    if len(text) <= 48:
        return f"{text}审批"
    return text[:60]


def _merge_task_name(content: str) -> str:
    """汇聚 USERTASK（taskKey 含 2）：parse 文字 + 强制「审批2」后缀（神笔重名规律）。"""
    name = _task_name_from_content(content or "总裁办主任")
    if re.search(r"审批2$", name):
        return name[:60]
    if name.endswith("审批"):
        return f"{name}2"[:60]
    return f"{name}审批2"[:60]


def _task_key_from_name(name: str, *, index: int) -> str:
    ascii_part = re.sub(r"[^\w]", "", name.encode("ascii", "ignore").decode())
    if ascii_part:
        return f"{ascii_part[:20].lower()}_{index}"
    return f"task_{hashlib.md5(name.encode()).hexdigest()[:8]}"


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
    text = (node.content or "").strip()
    if node.node_type == "抄送节点":
        return True
    if (node.shape or "").strip() == "波形":
        return bool(text) and "审批" not in text
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
        task_name = _task_name_from_content(text)
        ut = ctx.usertask(branch["id"], task_name=task_name[:60], task_key=branch_key)
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
    """P2c 统一入口。返回 (环节树, task_tree_source)。"""
    if not nodes:
        return None, "empty"

    from core.workflow.shenbi_builder import WORKFLOW_CATALOG

    meta = WORKFLOW_CATALOG.get(workflow_name) or {}
    flow_kind = str(meta.get("flow_kind") or "linear")

    if flow_kind == "complex_recruitment":
        tree = compile_recruitment_from_guide(
            nodes,
            proc_id=proc_id,
            settings=settings,
            start_task_name="申请填报",
        )
        return tree, "guide_dag_compiler:recruitment"

    graph = GuideGraph.build(nodes)
    if graph.fanout_groups():
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
