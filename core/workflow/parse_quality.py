"""VLM 流程图解析质量校验。"""

from __future__ import annotations

from schemas.workflow import WorkflowNodeItem


def title_parse_hints(title_hint: str) -> str:
    """按流程标题补充 VLM 识别提示，降低套用错误模板的概率。"""
    hint = (title_hint or "").strip()
    if not hint:
        return ""
    lines = [f"本图流程标题：{hint}"]
    if "借款" in hint:
        lines.append(
            "借款类流程图通常结构：发起人→部门经理→「子公司」菱形→多路公司/部门分支"
            "（含「分销网站」「丝路驿站」等嵌套菱形）→「金额」菱形→财务审批链→出纳。"
            "须按图中菱形内文字如实识别；不要臆造「是否OA」「预算审批」等其它表单字段。"
        )
    if "外贸" in hint and "报销" in hint:
        lines.append(
            "外贸板块费用报销结构：发起人→部门经理→「发起人是否是采购部」菱形"
            "（是→采购部经理+抄送；否→跳过）→「发起人自选费用部门」菱形→三路会计审批"
            "（博亚/欧亚/震宇等）→稽核主管→抄送→分管领导→「金额」菱形→「费用部门」出纳分支。"
            "费用部门菱形须识别为 3 路并行会计，勿与汇聚后的稽核主管混为同层分支。"
        )
    return "\n".join(lines)


def node_text_blob(nodes: list[WorkflowNodeItem]) -> str:
    return " ".join((n.content or "").strip() for n in nodes)


def validate_flow_parse(title_hint: str, nodes: list[WorkflowNodeItem]) -> tuple[bool, str]:
    """校验 parse 是否与标题/办事指南常见结构一致。"""
    if not nodes:
        return False, "未识别到有效节点"
    texts = node_text_blob(nodes)
    hint = (title_hint or "").strip()

    if "借款" in hint and ("文旅" in hint or "采购" in hint):
        wrong_template = ("是否OA" in texts or "预算审批" in texts) and "子公司" not in texts
        if wrong_template:
            return False, "识别结果含「是否OA/预算审批」但缺少「子公司」，与借款流程图不符"

    if "借款" in hint and "子公司" in texts:
        if "部门经理" not in texts and "发起人" not in texts:
            return False, "借款流程缺少发起人/部门经理节点"

    if "外贸" in hint and "报销" in hint:
        required = ("发起人是否是采购部", "发起人自选费用部门", "稽核主管")
        missing = [k for k in required if k not in texts]
        has_amount = "金额" in texts
        has_cashier_route = any(
            (n.content or "").strip() == "费用部门" and n.node_type == "判断条件" for n in nodes
        )
        if not has_amount and not has_cashier_route:
            missing.append("金额")
        if missing:
            return False, f"外贸费用报销缺少关键节点：{', '.join(missing)}"
        fybm = next((n for n in nodes if (n.content or "").strip() == "发起人自选费用部门"), None)
        if fybm is not None:
            branch_heads = [
                n
                for s in fybm.next_seq
                if (n := next((x for x in nodes if x.seq == s), None)) and n.node_type == "处理节点"
            ]
            if len(branch_heads) < 3:
                return False, f"费用部门分支仅识别到 {len(branch_heads)} 路，应为 3 路会计审批"

    return True, ""
