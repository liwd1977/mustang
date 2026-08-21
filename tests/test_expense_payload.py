"""集团费用报销单 payload 生成测试。"""

from __future__ import annotations

from core.workflow.complex_flow_builder import count_task_types
from core.workflow.shenbi_builder import build_workflow_payloads


def test_expense_payload_builds():
    payloads = build_workflow_payloads("集团费用报销单")
    assert payloads["task_tree_source"] == "guide_dag_compiler:expense"
    types = count_task_types(payloads["model"]["wfSimpleTaskInfo"])
    assert types.get("ROUTE", 0) >= 5
    assert types.get("BRANCHTASK", 0) >= 8
    form_group = next(g for g in payloads["model"]["formModel"]["group"] if g["label"] == "表单信息")
    props = {c.get("prop") for c in form_group["column"]}
    assert "fysy" in props
