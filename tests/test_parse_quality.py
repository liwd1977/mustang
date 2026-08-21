"""VLM 解析质量校验与金额分支。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workflow.guide_dag_compiler import GuideGraph, _diamond_branch_specs
from core.workflow.parse_quality import validate_flow_parse
from schemas.workflow import WorkflowNodeItem


def _oa_misparse_nodes() -> list[WorkflowNodeItem]:
    """VLM 误套用「是否OA」模板的 parse（文旅费用借款单）。"""
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "长方形", "node_type": "处理节点", "content": "发起人", "prev_seq": [1], "next_seq": [3]},
        {"seq": 3, "shape": "长方形", "node_type": "处理节点", "content": "部门经理", "prev_seq": [2], "next_seq": [4]},
        {"seq": 4, "shape": "菱形", "node_type": "判断条件", "content": "是否OA", "prev_seq": [3], "next_seq": [5, 6]},
        {"seq": 5, "shape": "长方形", "node_type": "处理节点", "content": "非OA", "prev_seq": [4], "next_seq": [7]},
        {"seq": 6, "shape": "菱形", "node_type": "判断条件", "content": "预算审批", "prev_seq": [4], "next_seq": [8, 9]},
    ]
    return [WorkflowNodeItem.model_validate(n) for n in raw]


def test_validate_rejects_oa_template_for_culture_loan():
    nodes = _oa_misparse_nodes()
    ok, reason = validate_flow_parse("文旅集团费用借款单", nodes)
    assert not ok
    assert "子公司" in reason


def test_validate_accepts_subsidiary_loan_dag():
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "长方形", "node_type": "处理节点", "content": "发起人", "prev_seq": [1], "next_seq": [3]},
        {"seq": 3, "shape": "长方形", "node_type": "处理节点", "content": "部门经理", "prev_seq": [2], "next_seq": [4]},
        {"seq": 4, "shape": "菱形", "node_type": "判断条件", "content": "子公司", "prev_seq": [3], "next_seq": [5, 6]},
        {"seq": 5, "shape": "长方形", "node_type": "处理节点", "content": "其他", "prev_seq": [4], "next_seq": [7]},
        {"seq": 6, "shape": "菱形", "node_type": "判断条件", "content": "分销网站", "prev_seq": [4], "next_seq": [8, 9]},
    ]
    nodes = [WorkflowNodeItem.model_validate(n) for n in raw]
    ok, _ = validate_flow_parse("文旅集团费用借款单", nodes)
    assert ok


def test_validate_foreign_trade_expense_requires_three_accountant_branches():
    raw = [
        {"seq": 1, "shape": "长方形", "node_type": "开始节点", "content": "开始", "prev_seq": [], "next_seq": [2]},
        {"seq": 2, "shape": "菱形", "node_type": "判断条件", "content": "发起人是否是采购部", "prev_seq": [1], "next_seq": [3, 4]},
        {"seq": 3, "shape": "菱形", "node_type": "判断条件", "content": "发起人自选费用部门", "prev_seq": [2], "next_seq": [5]},
        {"seq": 4, "shape": "长方形", "node_type": "处理节点", "content": "稽核主管", "prev_seq": [2], "next_seq": [6]},
        {"seq": 5, "shape": "长方形", "node_type": "处理节点", "content": "博亚会计", "prev_seq": [3], "next_seq": [4]},
        {"seq": 6, "shape": "菱形", "node_type": "判断条件", "content": "金额", "prev_seq": [4], "next_seq": [7, 8]},
    ]
    nodes = [WorkflowNodeItem.model_validate(n) for n in raw]
    ok, reason = validate_flow_parse("外贸板块费用报销单", nodes)
    assert not ok
    assert "3 路" in reason


def test_amount_branch_labels_from_node_text():
    diamond = WorkflowNodeItem(
        seq=10,
        shape="菱形",
        node_type="判断条件",
        content="金额",
        prev_seq=[3],
        next_seq=[11, 12],
    )
    n_lt = WorkflowNodeItem(
        seq=11, shape="长方形", node_type="处理节点", content="金额≤1000",
        prev_seq=[10], next_seq=[13],
    )
    n_gt = WorkflowNodeItem(
        seq=12, shape="长方形", node_type="处理节点", content="金额＞1000",
        prev_seq=[10], next_seq=[14],
    )
    graph = GuideGraph({10: diamond, 11: n_lt, 12: n_gt, 13: n_lt, 14: n_gt})
    specs = _diamond_branch_specs(graph, diamond)
    labels = [opt["label"] for _, _, opt in specs]
    assert any("1000" in lb and ("≤" in lb or "小于" in lb) for lb in labels)
    assert any("1000" in lb and ("＞" in lb or "大于" in lb) for lb in labels)
    assert labels[0] != labels[1]


@pytest.fixture(scope="module")
def expense_loan_cached_nodes() -> list[WorkflowNodeItem]:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        pytest.skip("缺少 parse 缓存")
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            if flow.get("image", {}).get("title_hint") == "文旅集团费用借款单":
                nodes = [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
                ok, _ = validate_flow_parse("文旅集团费用借款单", nodes)
                if not ok:
                    pytest.skip("缓存 parse 仍为错误模板，需重新 VLM 解析")
                return nodes
    pytest.skip("未找到文旅集团费用借款单 parse")


def test_cached_expense_loan_has_subsidiary_diamond(expense_loan_cached_nodes):
    texts = " ".join(n.content or "" for n in expense_loan_cached_nodes)
    assert "子公司" in texts
