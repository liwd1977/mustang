"""serial_dag 编译器：子公司费用报销等线性+侧抄送+条件汇聚。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks
from core.workflow.guide_dag_compiler import GuideGraph, compile_task_tree_from_guide, should_compile_serial_guide_dag
from schemas.workflow import WorkflowNodeItem


def _load_dingtai_expense_nodes() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            if flow.get("title") == "鼎泰伟业：费用报销单（已核准）" and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


@pytest.fixture(scope="module")
def dingtai_nodes() -> list[WorkflowNodeItem]:
    nodes = _load_dingtai_expense_nodes()
    if not nodes:
        pytest.skip("缺少鼎泰伟业费用报销 parse 缓存")
    return nodes


def test_dingtai_should_use_serial_dag(dingtai_nodes):
    graph = GuideGraph.build(dingtai_nodes)
    assert should_compile_serial_guide_dag(graph)


def test_dingtai_not_parallel_merge(dingtai_nodes):
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "鼎泰伟业：费用报销单",
        dingtai_nodes,
        proc_id="p-dingtai",
        settings=settings,
    )
    assert source == "guide_dag_compiler:serial_dag"
    assert source != "guide_dag_compiler:parallel_merge"
    types = count_task_types(root)
    assert types.get("ROUTE", 0) == 1
    assert types.get("BRANCHTASK", 0) == 2
    assert types.get("CCTASK", 0) >= 1

    names = [t.get("taskName") or "" for t in iter_all_tasks(root)]
    assert any("部门负责人" in n for n in names)
    assert any("会计" in n for n in names)
    assert any("财务经理" in n for n in names)
    assert not any("汇聚审批" in n for n in names)


def test_dingtai_expense_type_branch_conditions(dingtai_nodes):
    import json

    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "鼎泰伟业：费用报销单",
        dingtai_nodes,
        proc_id="p-dingtai",
        settings=settings,
    )
    assert source == "guide_dag_compiler:serial_dag"

    from core.workflow.complex_flow_builder import FYLX_FIELD

    branches: dict[str, str] = {}
    for task in iter_all_tasks(root):
        if task.get("type") != "BRANCHTASK":
            continue
        props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        conds = props.get("branchConditionList") or []
        if not conds or str(conds[0].get("fieldProp")) != FYLX_FIELD:
            continue
        raw = json.loads(str(conds[0].get("conditionValue") or "[]"))
        label = raw[0]["label"] if raw else ""
        child = task.get("child") or {}
        branches[label] = child.get("taskName") or ""

    assert branches.get("日常支付") and "业务负责人" in branches["日常支付"]
    assert branches.get("重大事项") and "总经理" in branches["重大事项"]


def _load_burqin_loan_nodes() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            title = flow.get("title") or ""
            if "布尔津" in title and "借款" in title and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


def test_burqin_loan_amount_branch():
    nodes = _load_burqin_loan_nodes()
    if not nodes:
        pytest.skip("缺少布尔津矿业领借款 parse 缓存")
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "外贸集团：布尔津矿业（领）借款单",
        nodes,
        proc_id="p-burqin",
        settings=settings,
    )
    assert source == "guide_dag_compiler:serial_dag"
    from core.workflow.complex_flow_builder import JEFD_FIELD

    lt_cc = gte_ut = False
    for task in iter_all_tasks(root):
        if task.get("type") != "BRANCHTASK":
            continue
        props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        conds = props.get("branchConditionList") or []
        if not conds or str(conds[0].get("fieldProp")) != JEFD_FIELD:
            continue
        val = str(conds[0].get("conditionValue") or "")
        child = task.get("child") or {}
        if '"value":"0"' in val or val.startswith('[{"label":"小于1万"'):
            lt_cc = child.get("type") == "CCTASK"
        if '"value":"1"' in val or "大于等于1万" in val:
            gte_ut = child.get("type") == "USERTASK"
    assert lt_cc and gte_ut


def _zhike_amount_nodes_both_cc_misparse() -> list[WorkflowNodeItem]:
    """VLM 将 >=1万 路径误标为波形抄送：两分支均应为 CC/审批区分。"""
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "长方形", "node_type": "处理节点", "content": "发起人", "prev_seq": [1], "next_seq": [3]},
        {"seq": 3, "shape": "长方形", "node_type": "处理节点", "content": "部门经理", "prev_seq": [2], "next_seq": [4]},
        {"seq": 4, "shape": "长方形", "node_type": "处理节点", "content": "野马智科总经理", "prev_seq": [3], "next_seq": [6]},
        {"seq": 5, "shape": "波形", "node_type": "抄送节点", "content": "野马集团财务总监，野马集团财务副经理（2人）", "prev_seq": [4], "next_seq": [], "parallel_seq": [6]},
        {"seq": 6, "shape": "菱形", "node_type": "判断条件", "content": "金额", "prev_seq": [4], "next_seq": [7, 8]},
        {"seq": 7, "shape": "波形", "node_type": "抄送节点", "content": "野马集团总经理", "prev_seq": [6], "next_seq": [9], "parallel_seq": [8]},
        {"seq": 8, "shape": "波形", "node_type": "抄送节点", "content": "野马集团总经理", "prev_seq": [6], "next_seq": [9], "parallel_seq": [7]},
        {"seq": 9, "shape": "长方形", "node_type": "处理节点", "content": "出纳", "prev_seq": [7, 8], "next_seq": []},
    ]
    return [WorkflowNodeItem.model_validate(n) for n in raw]


def _zhike_amount_nodes_side_cc_on_diamond() -> list[WorkflowNodeItem]:
    """侧向多角色抄送误挂在金额菱形下。"""
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "长方形", "node_type": "处理节点", "content": "发起人", "prev_seq": [1], "next_seq": [3]},
        {"seq": 3, "shape": "长方形", "node_type": "处理节点", "content": "野马智科总经理", "prev_seq": [2], "next_seq": [4]},
        {"seq": 4, "shape": "菱形", "node_type": "判断条件", "content": "金额", "prev_seq": [3], "next_seq": [5, 6, 7]},
        {"seq": 5, "shape": "波形", "node_type": "抄送节点", "content": "野马集团财务总监，野马集团财务副经理（2人）", "prev_seq": [4], "next_seq": [], "parallel_seq": [6, 7]},
        {"seq": 6, "shape": "波形", "node_type": "抄送节点", "content": "野马集团总经理", "prev_seq": [4], "next_seq": [8], "parallel_seq": [5, 7]},
        {"seq": 7, "shape": "长方形", "node_type": "处理节点", "content": "野马集团总经理", "prev_seq": [4], "next_seq": [8], "parallel_seq": [5, 6]},
        {"seq": 8, "shape": "长方形", "node_type": "处理节点", "content": "出纳", "prev_seq": [6, 7], "next_seq": []},
    ]
    return [WorkflowNodeItem.model_validate(n) for n in raw]


def test_amount_branch_when_both_paths_are_cc_nodes():
    nodes = _zhike_amount_nodes_both_cc_misparse()
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide("野马智科费用报销单", nodes, proc_id="p-zk", settings=settings)
    assert source == "guide_dag_compiler:serial_dag"
    from core.workflow.complex_flow_builder import JEFD_FIELD

    lt = gte = False
    for task in iter_all_tasks(root):
        if task.get("type") != "BRANCHTASK":
            continue
        props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        for cond in props.get("branchConditionList") or []:
            if str(cond.get("fieldProp")) != JEFD_FIELD:
                continue
            val = str(cond.get("conditionValue") or "")
            child = task.get("child") or {}
            if '"value":"0"' in val:
                lt = child.get("type") == "CCTASK"
            if '"value":"1"' in val or "大于等于" in val:
                gte = child.get("type") == "USERTASK"
    assert lt and gte


def test_amount_side_cc_not_in_branch_conditions():
    nodes = _zhike_amount_nodes_side_cc_on_diamond()
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, _ = compile_task_tree_from_guide("野马智科费用报销单", nodes, proc_id="p-zk2", settings=settings)
    from core.workflow.complex_flow_builder import JEFD_FIELD

    branch_children: list[tuple[str, str]] = []
    for task in iter_all_tasks(root):
        if task.get("type") != "BRANCHTASK":
            continue
        props = task.get("properties") if isinstance(task.get("properties"), dict) else {}
        for cond in props.get("branchConditionList") or []:
            if str(cond.get("fieldProp")) != JEFD_FIELD:
                continue
            child = task.get("child") or {}
            branch_children.append((str(child.get("type")), str(child.get("taskName") or "")))
    assert len(branch_children) == 2
    assert branch_children[0][0] == "CCTASK" and "总经理" in branch_children[0][1]
    assert branch_children[1][0] == "USERTASK" and "总经理" in branch_children[1][1]
    assert not any("财务总监" in name for _, name in branch_children)

    cc_names = [t.get("taskName") or "" for t in iter_all_tasks(root) if t.get("type") == "CCTASK"]
    assert any("财务总监" in n for n in cc_names)


def _load_seal_application_nodes() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_20260816_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            if "外贸印章用印" in str(flow.get("title") or "") and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


def test_seal_application_passthrough_merge():
    nodes = _load_seal_application_nodes()
    if not nodes:
        pytest.skip("缺少外贸印章用印 parse 缓存")
    settings = type("S", (), {"shenbi_tenant_id": "t1", "shenbi_environment": "ym"})()
    root, source = compile_task_tree_from_guide(
        "外贸印章用印申请单",
        nodes,
        proc_id="p-seal",
        settings=settings,
    )
    assert source == "guide_dag_compiler:serial_dag"

    names = [t.get("taskName") or "" for t in iter_all_tasks(root)]
    assert names.count("发起人部门领导审批") == 1
    assert any("发起部门分管领导审批" in n for n in names)
    assert any("总裁办主任审批" in n for n in names)
    assert any("野马集团行政专员审批" in n for n in names)
    assert any("文旅行政专员审批" in n for n in names)

    first_route = (root.get("child") or {}).get("conditions")
    assert first_route, "首层菱形应有两个分支"
    branch_names = [b.get("taskName") or "" for b in first_route]
    assert "对应公司/部门的领导" in branch_names
    assert "发起人部门领导" in branch_names
    dept_branch = next(b for b in first_route if "对应公司" in (b.get("taskName") or ""))
    child = dept_branch.get("child") or {}
    assert child.get("taskName") == "对应公司/部门的领导审批"
    assert not (child.get("child") or {}).get("taskName", "").startswith("发起人部门领导")

    merge_child = (root.get("child") or {}).get("child") or {}
    assert merge_child.get("taskName") == "发起人部门领导审批"
