"""外贸板块费用报销单：多菱形分支编译。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks
from core.workflow.guide_dag_compiler import compile_task_tree_from_guide
from schemas.workflow import WorkflowNodeItem


def _load_foreign_trade_expense_nodes() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            title = flow.get("title") or flow.get("image", {}).get("title_hint") or ""
            if "外贸板块费用报销" in title and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


@pytest.fixture(scope="module")
def ft_expense_nodes() -> list[WorkflowNodeItem]:
    nodes = _load_foreign_trade_expense_nodes()
    if not nodes:
        pytest.skip("缺少外贸板块费用报销 parse 缓存")
    return nodes


def test_foreign_trade_expense_uses_dedicated_compiler(ft_expense_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "外贸板块费用报销单",
        ft_expense_nodes,
        proc_id="p-ft",
        settings=settings,
    )
    assert source == "guide_dag_compiler:foreign_trade_expense"
    types = count_task_types(root)
    assert types.get("ROUTE", 0) >= 3
    assert types.get("BRANCHTASK", 0) >= 5

    branch_fields: set[str] = set()
    for task in iter_all_tasks(root):
        if task.get("type") != "BRANCHTASK":
            continue
        props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        for cond in props.get("branchConditionList") or []:
            prop = cond.get("fieldProp")
            if prop:
                branch_fields.add(str(prop))
    assert "sfscgb" in branch_fields
    assert "fybm" in branch_fields
    assert "jefd" in branch_fields

    names = [t.get("taskName") or "" for t in iter_all_tasks(root)]
    assert any("采购部经理" in n for n in names)
    assert any("博亚" in n or "费用会计" in n or "震宇" in n for n in names)
    assert any("稽核" in n for n in names)
    assert any("出纳" in n or "银行" in n for n in names)
    assert any("分管领导" in n for n in names)
    assert any("总经理" in n for n in names)

    # 采购部分支与「否」分支均汇入费用部门 ROUTE，再进入稽核
    mgr = next(t for t in iter_all_tasks(root) if t.get("taskName") == "发起人部门经理审批")
    route_cgb = mgr.get("child")
    assert route_cgb and route_cgb.get("type") == "ROUTE"
    route_fybm = route_cgb.get("child")
    assert route_fybm and route_fybm.get("type") == "ROUTE"
    assert len(route_fybm.get("conditions") or []) == 3
    fybm_children = [
        (c.get("child") or {}).get("taskName") or ""
        for c in route_fybm.get("conditions") or []
    ]
    assert sum(1 for n in fybm_children if "会计" in n) == 3
    assert route_fybm.get("child", {}).get("taskName") == "稽核主管审批"
    proc_branch = next(c for c in route_cgb.get("conditions") or [] if c.get("taskName") == "是")
    assert proc_branch.get("child", {}).get("taskName") == "野马集团采购部经理审批"


def test_foreign_trade_expense_form_has_fybm_company_options(ft_expense_nodes):
    from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model

    model = finalize_workflow_model(build_workflow_payloads("外贸板块费用报销单")["model"])
    form_cols = model["formModel"]["group"][0]["column"]
    fybm = next((c for c in form_cols if c.get("prop") == "fybm"), None)
    assert fybm is not None
    labels = {str(o.get("label") or "") for o in fybm.get("dicData") or []}
    assert "新疆野马博亚商贸有限公司" in labels


def test_branch_self_select_spec_for_expense_dept(ft_expense_nodes):
    from core.workflow.flow_semantics import collect_branch_self_select_specs

    specs = collect_branch_self_select_specs(ft_expense_nodes)
    assert len(specs) == 1
    assert specs[0].field_label == "费用部门"
    assert specs[0].field_prop == "fybm"
