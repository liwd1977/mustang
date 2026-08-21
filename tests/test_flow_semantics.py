"""办事指南流程语义转换单元测试。"""

from __future__ import annotations

from core.workflow.complex_flow_builder import _cc_task_name
from core.workflow.flow_semantics import guide_nodes_to_semantic_steps, semantic_steps_summary, strip_person_name
from core.workflow.shenbi_builder import _build_task_tree_from_guide_nodes
from schemas.workflow import WorkflowNodeItem


def _node(seq: int, content: str, *, shape: str = "长方形", node_type: str = "处理节点") -> WorkflowNodeItem:
    return WorkflowNodeItem(
        seq=seq,
        shape=shape,
        node_type=node_type,
        content=content,
        prev_seq=[seq - 1] if seq > 1 else [],
        next_seq=[seq + 1],
    )


def test_sample_car_contract_linear_semantics():
    nodes = [
        _node(1, "开始", shape="圆角矩形", node_type="开始节点"),
        _node(2, "发起人"),
        _node(3, "发起人自选部门领导"),
        _node(4, "发起人自选会计"),
        WorkflowNodeItem(
            seq=5,
            shape="波形",
            node_type="抄送节点",
            content="野马集团总经理\n发起人部门分管领导\n野马集团财务总监\n野马集团财务副经理（2人）\n核算会计（2人）",
            prev_seq=[4],
            next_seq=[],
        ),
    ]
    steps = guide_nodes_to_semantic_steps(nodes)
    chain = semantic_steps_summary(steps)

    assert chain == [
        "提交人",
        "部门经理审批（发起人指定）",
        "会计审批（发起人指定）",
        "抄送相关人员",
    ]
    cc = next(s for s in steps if s.kind == "cc")
    assert len(cc.cc_members) >= 3


def test_build_task_tree_linear_four_steps():
    nodes = [
        _node(1, "发起人"),
        _node(2, "发起人自选部门领导"),
        _node(3, "发起人自选会计"),
        WorkflowNodeItem(
            seq=4,
            shape="波形",
            node_type="抄送节点",
            content="野马集团总经理\n野马集团财务总监",
            prev_seq=[3],
            next_seq=[],
        ),
    ]
    root, _source = _build_task_tree_from_guide_nodes(
        nodes, proc_id="proc1", settings=type("S", (), {"shenbi_tenant_id": "t1"})(), workflow_name="外贸集团样车合同备案表"
    )
    assert root is not None
    assert root.get("taskKey") == "sqtb"
    assert root.get("taskName") == "提交人"
    child = root["child"]
    assert child.get("taskKey") == "todo"
    assert child.get("taskName") == "部门经理审批"
    assert child["child"].get("taskKey")
    assert child["child"].get("taskName") == "会计审批"
    assert child["child"]["child"].get("taskKey") == "cc"


def test_strip_person_name_cc_and_approval():
    assert strip_person_name("野马集团总裁办副主任 马杰") == "野马集团总裁办副主任"
    assert strip_person_name("陈刚 野马集团总经理") == "野马集团总经理"
    assert strip_person_name("房务总监（陈晶晶）") == "房务总监"
    assert strip_person_name("餐饮副总监（单鹏飞）") == "餐饮副总监"
    assert _cc_task_name("野马集团总裁办副主任 马杰") == "抄送野马集团总裁办副主任"
    assert "马杰" not in _cc_task_name("野马集团总裁办副主任 马杰")
