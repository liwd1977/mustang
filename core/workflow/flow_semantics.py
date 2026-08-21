"""办事指南流程图节点 → 神笔环节语义转换。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from schemas.workflow import WorkflowNodeItem

StepKind = Literal["start", "approval", "cc"]

_INITIATOR_ONLY = frozenset({"发起人", "发件人", "开始", "提交人"})
_SELF_SELECT_RE = re.compile(r"^(?:发起人|发件人)\s*自选\s*(.+)$")

# 环节名末尾若为人名（2~4 汉字且非职务词），剥离；具体人员由神笔运行时维护
_PERSON_NAME = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
_ROLE_MARKERS = frozenset(
    "部办司处科组队委办经理总监主任主管部长分管会计专员审批总经理总裁董事"
)

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


@dataclass(frozen=True)
class SelfSelectFieldSpec:
    """发起人自选环节：表单选人字段 + 对应审批环节。"""

    field_label: str
    field_prop: str
    task_name: str


@dataclass(frozen=True)
class BranchSelfSelectSpec:
    """发起人自选分支字段：仅表单 select/dic 字段，不创建审批环节（如费用部门）。"""

    field_label: str
    field_prop: str


# 分支自选字段 label → prop（与 complex_flow_builder 分支 ROUTE 字段一致）
BRANCH_SELF_SELECT_FIELD_PROPS: dict[str, str] = {
    "费用部门": "fybm",
}


# 自选角色 → 表单字段 prop（与神笔样车合同备案表 bmld/kj 对齐时可覆写 label）
SELF_SELECT_FIELD_PROPS: dict[str, str] = {
    "部门经理": "bmjl",
    "会计": "kj",
    "业务部门会计": "ywbmkj",
}


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


_PAREN_PERSON = re.compile(r"[（(]([\u4e00-\u9fff]{2,4})[）)]")


def strip_person_name(text: str) -> str:
    """去掉指南文字中的具体人名，保留职务/机构表述。"""
    text = (text or "").strip()
    if not text:
        return text
    # 括号内人名：「房务总监（陈晶晶）」→「房务总监」
    def _drop_paren_person(match: re.Match[str]) -> str:
        inner = match.group(1)
        if _PERSON_NAME.fullmatch(inner) and not any(m in inner for m in _ROLE_MARKERS):
            return ""
        return match.group(0)

    text = _PAREN_PERSON.sub(_drop_paren_person, text).strip()
    # 姓名在职务前：「陈刚 野马集团总经理」→「野马集团总经理」
    head_tail = text.split(maxsplit=1)
    if len(head_tail) == 2:
        head, tail = head_tail
        if _PERSON_NAME.fullmatch(head) and any(m in tail for m in _ROLE_MARKERS):
            text = tail
    # 姓名在职务后：「野马集团总裁办副主任 马杰」→「野马集团总裁办副主任」
    parts = text.rsplit(maxsplit=1)
    if len(parts) != 2:
        return text
    role, tail = parts
    if not role or not tail or not _PERSON_NAME.fullmatch(tail):
        return text
    if any(m in tail for m in _ROLE_MARKERS):
        return text
    return role


def _parse_self_select(text: str) -> tuple[str, bool] | None:
    m = _SELF_SELECT_RE.match(text)
    if not m:
        return None
    return _normalize_task_name(m.group(1)), True


def approval_task_name_from_content(text: str) -> str:
    """非自选的处理/审批环节名：去人名、补「审批」后缀。"""
    text = strip_person_name(text).strip()
    if not text:
        return "审批"
    if text.endswith("审批"):
        return text[:60]
    if len(text) <= 48:
        return f"{text}审批"
    return text[:60]


def usertask_spec_from_guide_content(content: str) -> tuple[str, bool]:
    """
    从指南 USERTASK 节点文字得到环节名与是否发起人自选办理人。

    - 「发起人自选 XXX」→ `{角色}审批` + assign_by_initiator=True（仅处理节点 / 角色）
    - 判断条件上的「发起人自选费用部门」等分支字段不走本函数，见 BranchSelfSelectSpec
    - 其余走 approval_task_name_from_content（去人名、补「审批」）
    """
    text = (content or "").strip()
    parsed = _parse_self_select(text)
    if parsed:
        return parsed
    return approval_task_name_from_content(text), False


def task_name_to_picker_label(task_name: str) -> str:
    """审批环节名 → 表单选人字段标签（去掉「审批」后缀）。"""
    text = (task_name or "").strip()
    if text.endswith("审批"):
        return text[:-2] or text
    return text


def self_select_field_spec_from_content(content: str) -> SelfSelectFieldSpec | None:
    """指南「发起人自选 XXX」处理节点 → 表单 flowUserChoose + 审批环节规格。"""
    task_name, assign = usertask_spec_from_guide_content(content)
    if not assign:
        return None
    field_label = task_name_to_picker_label(task_name)
    field_prop = SELF_SELECT_FIELD_PROPS.get(field_label, "")
    return SelfSelectFieldSpec(
        field_label=field_label,
        field_prop=field_prop,
        task_name=task_name,
    )


def branch_self_select_spec_from_content(content: str) -> BranchSelfSelectSpec | None:
    """指南判断条件「发起人自选 XXX」→ 仅表单分支 select 字段（非角色、无审批环节）。"""
    text = (content or "").strip()
    m = _SELF_SELECT_RE.match(text)
    if not m:
        return None
    field_label = m.group(1).strip()
    if field_label in SELF_SELECT_FIELD_PROPS or field_label.endswith("经理") or field_label.endswith("会计"):
        return None
    field_prop = BRANCH_SELF_SELECT_FIELD_PROPS.get(field_label, "")
    if not field_prop:
        return None
    return BranchSelfSelectSpec(field_label=field_label, field_prop=field_prop)


def collect_branch_self_select_specs(nodes: list[WorkflowNodeItem]) -> list[BranchSelfSelectSpec]:
    """从指南判断条件收集分支自选字段（如发起人自选费用部门）。"""
    specs: list[BranchSelfSelectSpec] = []
    seen: set[str] = set()
    for node in sorted(nodes, key=lambda n: n.seq):
        if node.node_type != "判断条件":
            continue
        spec = branch_self_select_spec_from_content(node.content or "")
        if spec is None or spec.field_prop in seen:
            continue
        seen.add(spec.field_prop)
        specs.append(spec)
    return specs


def collect_self_select_field_specs(nodes: list[WorkflowNodeItem]) -> list[SelfSelectFieldSpec]:
    """从指南节点收集全部自选环节（按 task_name 去重）。"""
    specs: list[SelfSelectFieldSpec] = []
    seen_tasks: set[str] = set()
    for node in sorted(nodes, key=lambda n: n.seq):
        if node.node_type != "处理节点":
            continue
        spec = self_select_field_spec_from_content(node.content or "")
        if spec is None or spec.task_name in seen_tasks:
            continue
        seen_tasks.add(spec.task_name)
        specs.append(spec)
    return specs


def _cc_task_name_for_role(role: str) -> str:
    text = strip_person_name((role or "").strip())
    if not text:
        return "抄送"
    if text.startswith("抄送"):
        return text[:60]
    return f"抄送{text}"[:60]


def collect_self_select_cc_specs(nodes: list[WorkflowNodeItem]) -> list[SelfSelectFieldSpec]:
    """
    发起人→审批横线上的「自选」：侧向抄送单角色（如业务部门会计）。
    需表单 flowUserChoose 字段，抄送环节绑定 fieldContactPerson。
    """
    by_seq = {n.seq: n for n in nodes}
    initiator_seqs = [
        n.seq
        for n in nodes
        if n.node_type == "处理节点" and _is_initiator_node(n.content or "")
    ]
    specs: list[SelfSelectFieldSpec] = []
    seen_tasks: set[str] = set()
    for init_seq in initiator_seqs:
        init_node = by_seq.get(init_seq)
        if init_node is None:
            continue
        for nxt_seq in init_node.next_seq:
            cc_node = by_seq.get(nxt_seq)
            if cc_node is None or not _is_cc_node(cc_node):
                continue
            if cc_node.prev_seq != [init_seq]:
                continue
            role_text = strip_person_name(cc_node.content or "")
            if not role_text or looks_like_multi_recipient_cc(role_text):
                continue
            task_name = _cc_task_name_for_role(role_text)
            if task_name in seen_tasks:
                continue
            specs.append(
                SelfSelectFieldSpec(
                    field_label=role_text,
                    field_prop=SELF_SELECT_FIELD_PROPS.get(role_text, ""),
                    task_name=task_name,
                )
            )
            seen_tasks.add(task_name)
    return specs


_CC_ROLE_HINTS = ("内勤", "副经理", "分管领导", "抄送", "知会")


def looks_like_multi_recipient_cc(text: str) -> bool:
    """长方形节点内多角色并列（如「内勤，副经理，分管领导」）视为抄送。"""
    raw = (text or "").strip()
    if not raw or "审批" in raw:
        return False
    if raw.startswith("抄送"):
        return True
    parts = re.split(r"[\n\r、，,;；]+", raw)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return False
    if not all(len(p) <= 24 for p in parts):
        return False
    return any(h in raw for h in _CC_ROLE_HINTS)


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
        line = re.sub(r"[（(][^）)]*[）)]", "", line).strip()
        line = re.sub(r"^抄送\s*", "", line).strip()
        line = strip_person_name(line)
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
