"""发起人自选：表单字段 + 审批环节绑定测试。"""

from __future__ import annotations

import json

from core.workflow.complex_flow_builder import iter_all_tasks
from core.workflow.flow_semantics import (
    collect_self_select_field_specs,
    self_select_field_spec_from_content,
)
from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model
from schemas.workflow import WorkflowNodeItem


def test_self_select_field_spec_from_guide_content():
    spec = self_select_field_spec_from_content("发起人自选部门经理")
    assert spec is not None
    assert spec.field_label == "部门经理"
    assert spec.task_name == "部门经理审批"
    assert spec.field_prop == "bmjl"


def test_sample_car_payload_self_select_field_and_person_link():
    model = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    form_cols = model["formModel"]["group"][0]["column"]
    labels = {str(c.get("label") or "") for c in form_cols}
    assert "部门经理" in labels
    assert "会计" in labels
    bmjl = next(c for c in form_cols if c.get("label") == "部门经理")
    assert bmjl.get("type") == "flowUserChoose"
    assert bmjl.get("prop") == "bmjl"

    visible = [c for c in form_cols if c.get("display") is not False]
    visible_labels = [str(c.get("label") or "") for c in visible]
    assert visible_labels.index("采购合同号") < visible_labels.index("部门经理")
    assert visible_labels.index("会计") > visible_labels.index("采购发票日期")

    mgr = next(
        t for t in iter_all_tasks(model["wfSimpleTaskInfo"]) if t.get("taskName") == "部门经理审批"
    )
    person = (mgr.get("properties") or {}).get("personList") or [{}]
    assert person[0].get("personType") == "fieldContactPerson"
    fc = json.loads(person[0]["fieldContact"])
    assert fc["prop"] == "bmjl"
    assert fc["label"] == "部门经理"


def test_expense_payload_self_select_dept_manager():
    model = finalize_workflow_model(build_workflow_payloads("集团费用报销单")["model"])
    form_cols = model["formModel"]["group"][0]["column"]
    assert any(c.get("label") == "部门经理" and c.get("type") == "flowUserChoose" for c in form_cols)
    mgr = next(
        t for t in iter_all_tasks(model["wfSimpleTaskInfo"]) if t.get("taskName") == "部门经理审批"
    )
    person = (mgr.get("properties") or {}).get("personList") or [{}]
    assert person[0].get("personType") == "fieldContactPerson"


def test_collect_self_select_specs_dedupes_by_task():
    nodes = [
        WorkflowNodeItem(
            seq=1,
            shape="长方形",
            node_type="处理节点",
            content="发起人自选部门经理",
            prev_seq=[],
            next_seq=[],
        ),
        WorkflowNodeItem(
            seq=2,
            shape="长方形",
            node_type="处理节点",
            content="发起人自选部门领导",
            prev_seq=[1],
            next_seq=[],
        ),
    ]
    specs = collect_self_select_field_specs(nodes)
    assert len(specs) == 1
    assert specs[0].task_name == "部门经理审批"
