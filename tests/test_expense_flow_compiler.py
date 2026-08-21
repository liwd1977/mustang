"""费用报销单条件 DAG 编译测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workflow.complex_flow_builder import CNLX_FIELD, FYSY_FIELD, JEFD_FIELD, SFZCB_FIELD, SFSJB_FIELD, count_task_types, iter_all_tasks
from core.workflow.expense_flow_compiler import compile_expense_from_guide, looks_like_expense_reimbursement_dag
from core.workflow.guide_dag_compiler import GuideGraph, compile_task_tree_from_guide
from schemas.workflow import WorkflowNodeItem


def _load_expense_nodes_from_cache() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            if flow.get("title") == "集团费用报销单（已核准）" and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


def _expense_guide_nodes() -> list[WorkflowNodeItem]:
    nodes = _load_expense_nodes_from_cache()
    if not nodes:
        pytest.skip("缺少费用报销 parse 缓存")
    return nodes


@pytest.fixture(scope="module")
def expense_nodes() -> list[WorkflowNodeItem]:
    return _expense_guide_nodes()


def test_expense_dag_detected(expense_nodes):
    graph = GuideGraph.build(expense_nodes)
    assert looks_like_expense_reimbursement_dag(graph)


def test_expense_compiler_not_parallel_merge(expense_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "集团费用报销单",
        expense_nodes,
        proc_id="p-expense",
        settings=settings,
    )
    assert source == "guide_dag_compiler:expense"
    types = count_task_types(root)
    assert types.get("ROUTE", 0) >= 5
    assert types.get("USERTASK", 0) >= 8
    assert types.get("CCTASK", 0) >= 5
    assert types.get("BRANCHTASK", 0) >= 8


def test_expense_branch_fields_bound(expense_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root = compile_expense_from_guide(expense_nodes, proc_id="p1", settings=settings)
    props: set[str] = set()
    for task in iter_all_tasks(root):
        if task.get("type") != "BRANCHTASK":
            continue
        for cond in (task.get("properties") or {}).get("branchConditionList") or []:
            props.add(str(cond.get("fieldProp") or ""))
    assert FYSY_FIELD in props
    assert SFZCB_FIELD in props
    assert JEFD_FIELD in props
    assert SFSJB_FIELD in props
    assert CNLX_FIELD in props


def test_expense_cc_titles_strip_headcount(expense_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root = compile_expense_from_guide(expense_nodes, proc_id="p1", settings=settings)
    cc_names = [t.get("taskName") or "" for t in iter_all_tasks(root) if t.get("type") == "CCTASK"]
    assert any("财务副经理" in name for name in cc_names)
    assert not any("2人" in name for name in cc_names)
    assert any("副主任" in name for name in cc_names)
    assert not any("马杰" in name for name in cc_names)


def _find_amount_audit_branch(root: dict, *, amount_key: str) -> dict:
    amount_branch = next(t for t in iter_all_tasks(root) if t.get("taskKey") == amount_key)
    audit_route = amount_branch.get("child")
    assert audit_route and audit_route.get("type") == "ROUTE"
    not_audit = next(c for c in audit_route.get("conditions") or [] if c.get("taskKey", "").startswith("bssjb"))
    return not_audit


def test_expense_lt_amount_audit_branch_is_cc(expense_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root = compile_expense_from_guide(expense_nodes, proc_id="p1", settings=settings)
    not_audit = _find_amount_audit_branch(root, amount_key="xy1w")
    gm = not_audit.get("child")
    assert gm is not None
    assert gm.get("type") == "CCTASK"
    assert "总经理" in (gm.get("taskName") or "")
    assert "陈刚" not in (gm.get("taskName") or "")


def test_expense_gte_amount_audit_branch_is_usertask(expense_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root = compile_expense_from_guide(expense_nodes, proc_id="p1", settings=settings)
    not_audit = _find_amount_audit_branch(root, amount_key="dydy1w")
    gm = not_audit.get("child")
    assert gm is not None
    assert gm.get("type") == "USERTASK"
    assert "总经理" in (gm.get("taskName") or "")
    assert "审批" in (gm.get("taskName") or "")
    assert "陈刚" not in (gm.get("taskName") or "")

    audit_route = next(t for t in iter_all_tasks(root) if t.get("taskKey") == "dydy1w")["child"]
    is_audit = next(c for c in audit_route.get("conditions") or [] if c.get("taskKey", "").startswith("sfsjb2"))
    president = is_audit.get("child")
    assert president is not None
    assert president.get("type") == "USERTASK"
    assert "总裁" in (president.get("taskName") or "")
    assert "陈强" not in (president.get("taskName") or "")
