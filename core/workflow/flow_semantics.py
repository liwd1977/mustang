"""办事指南流程图节点 → 神笔环节语义转换。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from schemas.workflow import WorkflowNodeItem

StepKind = Literal["start", "approval", "cc"]

_INITIATOR_ONLY = frozenset({"发起人", "发件人", "开始", "提交人"})
_SELF_SELECT_RE = re.compile(r"^(?:发起人|发件人)\s*自选\s*(.+)$")

# 指南角色表述 → 神笔标准环节名
_ROLE_TASK_NAMES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"部门领导|部门经理|分管领导"), "部门经理审批"),
    (re.compile(r"会计|核算会计"), "会计审批"),
    (re.compile(r"出纳"), "出纳审批"),
    (re.compile(r"财务总监"), "财务总监审批"),
    (re.compile(r"总经理"), "总经理审批"),
]


@dataclass
class SemanticStep:
    kind: StepKind
    task_name: str
    assign_by_initiator: bool = False
    cc_members: list[str] = field(default_factory=list)
    raw_content: str = ""


def _content_text(node: WorkflowNodeItem) -> str:
    return (node.content or "").strip()


def _is_initiator_node(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return compact in _INITIATOR_ONLY or compact in {"发起人节点", "发件人节点"}


def _is_cc_node(node: WorkflowNodeItem) -> bool:
    if node.node_type == "抄送节点":
        return True
    if node.shape == "波形":
        return True
    text = _content_text(node)
    if not text:
        return False
    if "抄送" in text and not _SELF_SELECT_RE.match(text):
        return True
    # 波形节点常含多行职务名单
    if node.shape == "波形" or "\n" in text:
        lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]
        if len(lines) >= 2:
            return True
    return False


def _normalize_task_name(role_text: str) -> str:
    role = role_text.strip()
    for pattern, name in _ROLE_TASK_NAMES:
        if pattern.search(role):
            return name
    role = re.sub(r"^[发起人发件人自选\s]+", "", role)
    if role and not role.endswith("审批"):
        return f"{role}审批"
    return role or "审批"


def _parse_self_select(text: str) -> tuple[str, bool] | None:
    m = _SELF_SELECT_RE.match(text)
    if not m:
        return None
    return _normalize_task_name(m.group(1)), True


def split_cc_recipients(text: str) -> list[str]:
    """将指南抄送节点文字按常见分隔符拆成多个抄送对象。"""
    parts = re.split(r"[\n\r、，,;；]+", text or "")
    members: list[str] = []
    for part in parts:
        line = part.strip()
        if not line:
            continue
        line = re.sub(r"^\d+[.)、]?\s*", "", line)
        line = re.sub(r"[（(]\d+人[）)]", "", line).strip()
        line = re.sub(r"^抄送\s*", "", line).strip()
        if line and line not in members:
            members.append(line)
    return members


def _split_cc_members(text: str) -> list[str]:
    return split_cc_recipients(text)


def _flow_has_branching(nodes: list[WorkflowNodeItem]) -> bool:
    for node in nodes:
        if node.node_type == "判断条件":
            return True
        if len(node.next_seq) > 1:
            return True
    return False


def guide_nodes_to_semantic_steps(nodes: list[WorkflowNodeItem]) -> list[SemanticStep]:
    """
    将办事指南 VLM 节点转为线性神笔环节语义。

    规则摘要：
    - 发起人/开始 → 提交人（START），不单独建审批节点
    - 「发起人自选 XXX」→ XXX 审批 + 发起人指定办理人
    - 线性流程忽略误判的判断条件节点
    - 抄送/波形节点合并为一个抄送环节（验证期用单人占位名）
    """
    if not nodes:
        return []

    ordered = sorted(nodes, key=lambda n: n.seq)
    linear = not _flow_has_branching(ordered)
    steps: list[SemanticStep] = []
    pending_cc_members: list[str] = []

    def flush_cc() -> None:
        nonlocal pending_cc_members
        if not pending_cc_members and not any(s.kind == "cc" for s in steps):
            return
        members = list(dict.fromkeys(pending_cc_members))
        steps.append(
            SemanticStep(
                kind="cc",
                task_name="抄送相关人员",
                cc_members=members,
                raw_content="; ".join(members),
            )
        )
        pending_cc_members = []

    for node in ordered:
        if node.node_type in ("开始节点", "结束节点"):
            continue
        if linear and node.node_type == "判断条件":
            continue

        text = _content_text(node)

        if _is_cc_node(node):
            pending_cc_members.extend(_split_cc_members(text))
            continue

        if _is_initiator_node(text):
            if not steps or steps[0].kind != "start":
                steps.insert(0, SemanticStep(kind="start", task_name="提交人", raw_content=text))
            continue

        self_select = _parse_self_select(text)
        if self_select:
            task_name, assign = self_select
            steps.append(
                SemanticStep(
                    kind="approval",
                    task_name=task_name,
                    assign_by_initiator=assign,
                    raw_content=text,
                )
            )
            continue

        if node.node_type == "判断条件":
            # 含分支的流程暂保留条件名（后续扩展）
            steps.append(
                SemanticStep(
                    kind="approval",
                    task_name=f"条件-{text or node.seq}"[:60],
                    raw_content=text,
                )
            )
            continue

        if node.node_type == "处理节点" and text:
            assign = "自选" in text
            task_name = _normalize_task_name(text) if assign else (text[:60] if len(text) <= 60 else text[:57] + "...")
            if assign:
                parsed = _parse_self_select(text) or _parse_self_select(f"发起人自选{text}")
                if parsed:
                    task_name, assign = parsed
            steps.append(
                SemanticStep(
                    kind="approval",
                    task_name=task_name,
                    assign_by_initiator=assign,
                    raw_content=text,
                )
            )

    flush_cc()

    if not steps or steps[0].kind != "start":
        steps.insert(0, SemanticStep(kind="start", task_name="提交人", raw_content="发起人"))

    # 抄送环节统一放到末尾
    cc_steps = [s for s in steps if s.kind == "cc"]
    other = [s for s in steps if s.kind != "cc"]
    if cc_steps:
        merged_members: list[str] = []
        for s in cc_steps:
            merged_members.extend(s.cc_members)
        other.append(
            SemanticStep(
                kind="cc",
                task_name="抄送相关人员",
                cc_members=list(dict.fromkeys(merged_members)),
            )
        )
    return other


def semantic_steps_summary(steps: list[SemanticStep]) -> list[str]:
    lines: list[str] = []
    for step in steps:
        if step.kind == "start":
            lines.append("提交人")
        elif step.kind == "approval":
            suffix = "（发起人指定）" if step.assign_by_initiator else ""
            lines.append(f"{step.task_name}{suffix}")
        else:
            lines.append(step.task_name)
    return lines
