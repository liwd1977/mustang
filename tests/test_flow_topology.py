"""环节树拓扑推断测试。"""

from __future__ import annotations

from core.workflow.flow_topology import (
    looks_like_recruitment_dag,
    task_tree_degraded,
    task_tree_has_routes,
)
from core.workflow.guide_dag_compiler import compile_task_tree_from_guide
from schemas.workflow import WorkflowNodeItem


def _settings():
    return type("S", (), {"shenbi_tenant_id": "t1"})()


def test_looks_like_recruitment_dag():
    nodes = [
        WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        WorkflowNodeItem(seq=2, shape="菱形", node_type="判断条件", content="集团二线", prev_seq=[1], next_seq=[3, 4, 5, 6]),
        WorkflowNodeItem(seq=3, shape="长方形", node_type="处理节点", content="集团党委办公室", prev_seq=[2], next_seq=[7]),
        WorkflowNodeItem(seq=4, shape="长方形", node_type="处理节点", content="总裁办", prev_seq=[2], next_seq=[7]),
        WorkflowNodeItem(seq=5, shape="长方形", node_type="处理节点", content="董办", prev_seq=[2], next_seq=[7]),
        WorkflowNodeItem(seq=6, shape="长方形", node_type="处理节点", content="集团财务部", prev_seq=[2], next_seq=[7]),
        WorkflowNodeItem(seq=7, shape="长方形", node_type="处理节点", content="汇聚", prev_seq=[3, 4, 5, 6], next_seq=[]),
    ]
    assert looks_like_recruitment_dag(nodes)


def test_linear_not_recruitment():
    nodes = [
        WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="发起人", prev_seq=[1], next_seq=[3]),
        WorkflowNodeItem(seq=3, shape="长方形", node_type="处理节点", content="部门经理", prev_seq=[2], next_seq=[]),
    ]
    assert not looks_like_recruitment_dag(nodes)
    root, source = compile_task_tree_from_guide("外贸集团样车合同备案表", nodes, proc_id="p1", settings=_settings())
    assert source == "guide_dag_compiler:linear"
    assert not task_tree_has_routes(root)


def test_task_tree_degraded_detects_shell():
    local = {
        "type": "STARTTASK",
        "taskKey": "sqtb",
        "child": {
            "type": "ROUTE",
            "conditions": [{}, {}, {}, {}],
            "child": {"type": "USERTASK", "taskKey": "merge"},
        },
    }
    shell = {"type": "STARTTASK", "taskKey": "sqtb", "taskName": "办理start", "child": None}
    assert task_tree_degraded(shell, local)
    assert not task_tree_degraded(local, local)
