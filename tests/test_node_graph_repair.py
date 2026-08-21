"""节点图后处理：误挂抄送截断等。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks
from core.workflow.guide_dag_compiler import compile_task_tree_from_guide
from core.workflow.node_graph import finalize_workflow_nodes
from schemas.workflow import WorkflowNodeItem


def _procurement_loan_misparse_nodes() -> list[WorkflowNodeItem]:
    """VLM 将侧向抄送误挂在「子公司」菱形下（文旅集团采购借款单）。"""
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "长方形", "node_type": "处理节点", "content": "发起人", "prev_seq": [1], "next_seq": [3]},
        {"seq": 3, "shape": "长方形", "node_type": "处理节点", "content": "野马集团采购部经理", "prev_seq": [2], "next_seq": [4]},
        {"seq": 4, "shape": "菱形", "node_type": "判断条件", "content": "子公司", "prev_seq": [3], "next_seq": [5, 6]},
        {
            "seq": 5,
            "shape": "长方形",
            "node_type": "处理节点",
            "content": "采购部内勤，采购部副经理，采购部分管领导",
            "prev_seq": [4],
            "next_seq": [],
        },
        {
            "seq": 6,
            "shape": "长方形",
            "node_type": "处理节点",
            "content": "马业部 开曲马公司 时代体育公司",
            "prev_seq": [4],
            "next_seq": [7],
        },
        {"seq": 7, "shape": "长方形", "node_type": "处理节点", "content": "房务总监", "prev_seq": [6], "next_seq": []},
    ]
    return [WorkflowNodeItem.model_validate(n) for n in raw]


def test_repair_truncates_subsidiary_diamond_with_side_cc():
    nodes = finalize_workflow_nodes(_procurement_loan_misparse_nodes())
    seqs = {n.seq for n in nodes}
    assert 4 not in seqs
    assert 6 not in seqs
    assert 7 not in seqs
    assert 5 in seqs
    cc = next(n for n in nodes if n.seq == 5)
    assert cc.node_type == "抄送节点"
    mgr = next(n for n in nodes if n.seq == 3)
    assert mgr.next_seq == [5]


def test_procurement_loan_compiles_as_linear_with_cc():
    nodes = finalize_workflow_nodes(_procurement_loan_misparse_nodes())
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "文旅集团采购借款单",
        nodes,
        proc_id="p-wl",
        settings=settings,
    )
    assert source == "guide_dag_compiler:linear"
    types = count_task_types(root)
    assert types.get("ROUTE", 0) == 0
    assert types.get("CCTASK", 0) >= 1
    names = [t.get("taskName") or "" for t in iter_all_tasks(root)]
    assert any("采购部经理" in n for n in names)
    assert any("内勤" in n or "抄送" in n for n in names)


def _load_procurement_loan_nodes() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            hint = flow.get("image", {}).get("title_hint") or flow.get("title") or ""
            if hint == "文旅集团采购借款单" and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


@pytest.fixture(scope="module")
def procurement_loan_nodes() -> list[WorkflowNodeItem]:
    nodes = _load_procurement_loan_nodes()
    if not nodes:
        pytest.skip("缺少文旅集团采购借款单 parse 缓存")
    return nodes


def test_procurement_loan_full_dag_not_linear(procurement_loan_nodes):
    """完整 VLM 解析（39 节点）应编译为多 ROUTE 复杂流程，而非线性。"""
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, source = compile_task_tree_from_guide(
        "文旅集团采购借款单",
        procurement_loan_nodes,
        proc_id="p-wl-full",
        settings=settings,
    )
    assert source == "guide_dag_compiler:serial_dag"
    types = count_task_types(root)
    assert types.get("ROUTE", 0) >= 3
    assert types.get("BRANCHTASK", 0) >= 4
    assert types.get("USERTASK", 0) >= 5
    assert len(list(iter_all_tasks(root))) >= 20


def test_procurement_loan_subsidiary_and_silk_road_three_routes(procurement_loan_nodes):
    """子公司 3 路 + 丝路驿站嵌套 3 路（含连线标签 其他/龙馆）。"""
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root, _ = compile_task_tree_from_guide(
        "文旅集团采购借款单",
        procurement_loan_nodes,
        proc_id="p-wl-routes",
        settings=settings,
    )

    def find_route_after(start: dict, depth: int = 0) -> dict | None:
        node = start
        steps = 0
        while node and steps < depth:
            node = node.get("child")
            steps += 1
        return node if node and node.get("type") == "ROUTE" else None

    mgr = next(t for t in iter_all_tasks(root) if "采购部经理" in (t.get("taskName") or ""))
    route_zgs = mgr.get("child")
    assert route_zgs and route_zgs.get("type") == "ROUTE"
    zgs_names = [c.get("taskName") for c in route_zgs.get("conditions") or []]
    assert zgs_names == ["其他", "马业部", "丝路驿站"]

    silk_branch = next(c for c in route_zgs.get("conditions") or [] if c.get("taskName") == "丝路驿站")
    route_slyz = silk_branch.get("child")
    assert route_slyz and route_slyz.get("type") == "ROUTE"
    slyz_names = [c.get("taskName") for c in route_slyz.get("conditions") or []]
    assert slyz_names == ["龙馆", "其他", "料场/马背/餐饮"]

    other_branch = next(c for c in route_slyz.get("conditions") or [] if c.get("taskName") == "其他")
    other_ut = other_branch.get("child") or {}
    assert other_ut.get("type") == "USERTASK"
    assert "房务总监" in (other_ut.get("taskName") or "")
    assert "陈晶晶" not in (other_ut.get("taskName") or "")

    maye_branch = next(c for c in route_zgs.get("conditions") or [] if c.get("taskName") == "马业部")
    maye_child = maye_branch.get("child") or {}
    assert maye_child.get("type") == "ROUTE"
    maye_tasks = [t.get("taskName") or "" for t in iter_all_tasks(maye_child)]
    assert any("文旅集团总经理" in n for n in maye_tasks)

    route_slyz_merge = route_slyz.get("child") or {}
    assert route_slyz_merge.get("type") == "ROUTE"
    slyz_amt_tasks = [t.get("taskName") or "" for t in iter_all_tasks(route_slyz_merge)]
    assert any("文旅集团总经理" in n for n in slyz_amt_tasks)

    zgs_other = next(c for c in route_zgs.get("conditions") or [] if c.get("taskName") == "其他")
    assert zgs_other.get("child") is None

    zgs_merge = route_zgs.get("child") or {}
    assert zgs_merge.get("type") == "ROUTE"
    zgs_amt_tasks = [t.get("taskName") or "" for t in iter_all_tasks(zgs_merge)]
    assert any("野马集团总经理" in n for n in zgs_amt_tasks)
    assert "物业公司" not in (zgs_merge.get("taskName") or "")
    for t in iter_all_tasks(root):
        name = t.get("taskName") or ""
        assert "陈晶晶" not in name
        assert "单鹏飞" not in name
        assert "出纳" not in name
        assert "野马时光" not in name.replace("\n", "")

    dept_route = zgs_merge.get("child") or {}
    assert dept_route.get("type") == "ROUTE"
    dept_branches = [c.get("taskName") for c in dept_route.get("conditions") or []]
    assert dept_branches == ["物业公司", "其他", "工坊文化创意", "丝路驿站", "餐饮管理公司"]
    all_names = [t.get("taskName") or "" for t in iter_all_tasks(root)]
    assert any("物业公司会计" in n for n in all_names)
    assert any("文化公司主管会计" in n for n in all_names)
    assert any("文旅文化财务副经理" in n for n in all_names)
    assert any("文旅酒店财务副经理" in n for n in all_names)
    assert any("野马集团财务总监" in n or "抄送野马集团财务总监" in n for n in all_names)
    assert not any("出纳" in n for n in all_names)


def _foreign_trade_miswired_nodes() -> list[WorkflowNodeItem]:
    """采购部抄送误连稽核主管、跳过费用部门菱形。"""
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "长方形", "node_type": "处理节点", "content": "发起人", "prev_seq": [1], "next_seq": [3]},
        {"seq": 3, "shape": "长方形", "node_type": "处理节点", "content": "发起人部门经理", "prev_seq": [2], "next_seq": [4]},
        {"seq": 4, "shape": "菱形", "node_type": "判断条件", "content": "发起人是否是采购部", "prev_seq": [3], "next_seq": [5, 6]},
        {"seq": 5, "shape": "长方形", "node_type": "处理节点", "content": "野马集团采购部经理", "prev_seq": [4], "next_seq": [7]},
        {"seq": 6, "shape": "菱形", "node_type": "判断条件", "content": "发起人自选费用部门", "prev_seq": [4], "next_seq": [8, 9, 10]},
        {"seq": 7, "shape": "波形", "node_type": "抄送节点", "content": "采购部内勤", "prev_seq": [5], "next_seq": [11]},
        {"seq": 8, "shape": "长方形", "node_type": "处理节点", "content": "博亚会计 张佳佳", "prev_seq": [6], "next_seq": [11]},
        {"seq": 9, "shape": "长方形", "node_type": "处理节点", "content": "费用会计 林婷婷", "prev_seq": [6], "next_seq": [11]},
        {"seq": 10, "shape": "长方形", "node_type": "处理节点", "content": "震宇会计 贾熙熙", "prev_seq": [6], "next_seq": [11]},
        {"seq": 11, "shape": "长方形", "node_type": "处理节点", "content": "稽核主管", "prev_seq": [7, 8, 9, 10], "next_seq": []},
    ]
    return [WorkflowNodeItem.model_validate(n) for n in raw]


def test_repair_foreign_trade_proc_cc_rejoins_fybm():
    nodes = finalize_workflow_nodes(_foreign_trade_miswired_nodes())
    cc = next(n for n in nodes if n.seq == 7)
    fybm = next(n for n in nodes if n.seq == 6)
    audit = next(n for n in nodes if n.seq == 11)
    assert cc.next_seq == [6]
    assert 7 in fybm.prev_seq
    assert 7 not in audit.prev_seq
