"""P2 指南 DAG 编译器测试。"""

from __future__ import annotations

from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks
from core.workflow.guide_dag_compiler import (
    GuideGraph,
    compile_recruitment_from_guide,
    compile_task_tree_from_guide,
    recruitment_branch_fields_bound,
    summarize_recruitment_branch_plans,
)
from core.workflow.shenbi_client import _response_looks_like_shell, response_has_task_tree
from schemas.workflow import WorkflowNodeItem


def _settings():
    return type("S", (), {"shenbi_tenant_id": "t1"})()


def _recruitment_guide_nodes() -> list[WorkflowNodeItem]:
    """简化招聘指南 DAG：8 路并行 + 汇聚 + 尾部审批。"""
    return [
        WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        WorkflowNodeItem(seq=2, shape="菱形", node_type="判断条件", content="集团二线", prev_seq=[1], next_seq=[3, 4, 5, 6, 7, 8, 9, 10]),
        WorkflowNodeItem(seq=3, shape="长方形", node_type="处理节点", content="集团党委办公室", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=4, shape="长方形", node_type="处理节点", content="总裁办主任", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=5, shape="长方形", node_type="处理节点", content="董办", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=6, shape="长方形", node_type="处理节点", content="集团财务部", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=7, shape="长方形", node_type="处理节点", content="集团采购部", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=8, shape="长方形", node_type="处理节点", content="集团纪检部", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=9, shape="长方形", node_type="处理节点", content="集团审计部", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=10, shape="长方形", node_type="处理节点", content="集团工会", prev_seq=[2], next_seq=[11]),
        WorkflowNodeItem(seq=11, shape="长方形", node_type="处理节点", content="总裁办主任审批2", prev_seq=[3, 4, 5, 6, 7, 8, 9, 10], next_seq=[12]),
        WorkflowNodeItem(seq=12, shape="菱形", node_type="判断条件", content="是否审计部", prev_seq=[11], next_seq=[13, 14]),
        WorkflowNodeItem(seq=13, shape="菱形", node_type="判断条件", content="经理及以上", prev_seq=[12], next_seq=[15, 16]),
        WorkflowNodeItem(seq=14, shape="长方形", node_type="处理节点", content="野马集团总裁审批", prev_seq=[12], next_seq=[17]),
        WorkflowNodeItem(seq=15, shape="长方形", node_type="处理节点", content="野马集团总经理审批", prev_seq=[13], next_seq=[17]),
        WorkflowNodeItem(seq=16, shape="波形", node_type="抄送节点", content="抄送野马集团总经理", prev_seq=[13], next_seq=[17]),
        WorkflowNodeItem(seq=17, shape="长方形", node_type="处理节点", content="野马集团总裁办副主任审批", prev_seq=[14, 15, 16], next_seq=[19]),
        WorkflowNodeItem(
            seq=19,
            shape="波形",
            node_type="抄送节点",
            content="野马集团招聘主管、野马集团招聘专员",
            prev_seq=[17],
            next_seq=[18],
        ),
        WorkflowNodeItem(seq=18, shape="长方形", node_type="处理节点", content="野马集团人事经理审批", prev_seq=[19], next_seq=[]),
    ]


def test_guide_graph_fanout():
    graph = GuideGraph.build(_recruitment_guide_nodes())
    groups = graph.fanout_groups()
    assert any(len(g) >= 8 for g in groups)


def test_p2_recruitment_compiler_topology():
    root = compile_recruitment_from_guide(_recruitment_guide_nodes(), proc_id="p1", settings=_settings())
    types = count_task_types(root)
    assert types["STARTTASK"] == 1
    assert types["ROUTE"] == 4
    assert types["USERTASK"] == 13
    assert types["CCTASK"] >= 3
    assert types["BRANCHTASK"] == 12
    assert root["taskName"] == "申请填报"
    assert len(root["child"]["conditions"]) == 8
    assert recruitment_branch_fields_bound(root)


def test_p2c_unified_entry_recruitment():
    root, source = compile_task_tree_from_guide(
        "野马集团二线招聘需求表",
        _recruitment_guide_nodes(),
        proc_id="p1",
        settings=_settings(),
    )
    assert root is not None
    assert source == "guide_dag_compiler:recruitment"
    assert count_task_types(root)["ROUTE"] == 4


def test_p2c_unified_entry_linear():
    nodes = [
        WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="发起人", prev_seq=[1], next_seq=[3]),
        WorkflowNodeItem(
            seq=3,
            shape="长方形",
            node_type="处理节点",
            content="发起人自选部门领导",
            prev_seq=[2],
            next_seq=[4],
        ),
        WorkflowNodeItem(
            seq=4,
            shape="波形",
            node_type="抄送节点",
            content="野马集团总经理",
            prev_seq=[3],
            next_seq=[],
        ),
    ]
    root, source = compile_task_tree_from_guide(
        "外贸集团样车合同备案表",
        nodes,
        proc_id="p1",
        settings=_settings(),
    )
    assert root is not None
    assert source == "guide_dag_compiler:linear"
    assert root["type"] == "STARTTASK"


def test_p2_position_mapping_real_parse_shape():
    """真实 parse 结构：8 路 fan-out 按顺序绑定 zpbm，而非文字误匹配。"""
    nodes = [
        WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="发起人", prev_seq=[1], next_seq=[3]),
        WorkflowNodeItem(
            seq=3,
            shape="菱形",
            node_type="判断条件",
            content="集团二线",
            prev_seq=[2],
            next_seq=[4, 5, 6, 7, 8, 9, 10, 11],
        ),
        WorkflowNodeItem(seq=4, shape="长方形", node_type="处理节点", content="集团工会党办", prev_seq=[3], next_seq=[12]),
        WorkflowNodeItem(seq=5, shape="长方形", node_type="处理节点", content="总裁办主任", prev_seq=[3], next_seq=[12]),
        WorkflowNodeItem(seq=6, shape="长方形", node_type="处理节点", content="董办主任", prev_seq=[3], next_seq=[12]),
        WorkflowNodeItem(seq=7, shape="长方形", node_type="处理节点", content="财务副总 张婷婷", prev_seq=[3], next_seq=[13]),
        WorkflowNodeItem(seq=8, shape="长方形", node_type="处理节点", content="采购经理", prev_seq=[3], next_seq=[14]),
        WorkflowNodeItem(seq=9, shape="长方形", node_type="处理节点", content="纪检部长", prev_seq=[3], next_seq=[12]),
        WorkflowNodeItem(seq=10, shape="长方形", node_type="处理节点", content="审计经理", prev_seq=[3], next_seq=[12]),
        WorkflowNodeItem(seq=11, shape="长方形", node_type="处理节点", content="集团工会党办", prev_seq=[3], next_seq=[12]),
        WorkflowNodeItem(
            seq=12,
            shape="长方形",
            node_type="处理节点",
            content="总裁办主任",
            prev_seq=[4, 5, 6, 9, 10, 11],
            next_seq=[15],
        ),
        WorkflowNodeItem(seq=13, shape="长方形", node_type="处理节点", content="财务总监", prev_seq=[7], next_seq=[12]),
        WorkflowNodeItem(seq=14, shape="长方形", node_type="处理节点", content="采购分管", prev_seq=[8], next_seq=[12]),
        WorkflowNodeItem(seq=15, shape="菱形", node_type="判断条件", content="是否审计部", prev_seq=[12], next_seq=[]),
    ]
    bindings = summarize_recruitment_branch_plans(nodes)
    assert len(bindings) == 8
    assert bindings[0]["dept_label"] == "集团党委办公室"
    assert bindings[1]["guide_seqs"] == [5]
    assert bindings[1]["task_names"] == ["总裁办主任审批"]
    assert bindings[7]["dept_label"] == "集团工会"


def test_p2_branch_names_follow_parse_content():
    """加工层环节名忠实 parse：改为副主任则输出副主任，不擅自替换。"""
    nodes = _recruitment_guide_nodes()
    nodes = [
        n.model_copy(update={"content": "总裁办副主任"}) if n.seq == 4 else n
        for n in nodes
    ]
    root = compile_recruitment_from_guide(nodes, proc_id="p1", settings=_settings())
    zcb = next(t for t in iter_all_tasks(root) if t.get("taskKey") == "zcbzrsp")
    assert zcb.get("taskName") == "总裁办副主任审批"
    nodes_default = _recruitment_guide_nodes()
    root2 = compile_recruitment_from_guide(nodes_default, proc_id="p1", settings=_settings())
    zcb2 = next(t for t in iter_all_tasks(root2) if t.get("taskKey") == "zcbzrsp")
    assert zcb2.get("taskName") == "总裁办主任审批"


def test_p2_finance_chain_from_guide():
    nodes = _recruitment_guide_nodes()
    updated: list[WorkflowNodeItem] = []
    for n in nodes:
        if n.seq == 6:
            updated.append(
                n.model_copy(
                    update={
                        "content": "野马集团财务副总经理 张婷婷",
                        "next_seq": [19],
                    }
                )
            )
        else:
            updated.append(n)
    updated.append(
        WorkflowNodeItem(
            seq=19,
            shape="长方形",
            node_type="处理节点",
            content="野马集团财务总监",
            prev_seq=[6],
            next_seq=[11],
        )
    )
    root = compile_recruitment_from_guide(updated, proc_id="p1", settings=_settings())
    names = [t.get("taskName") for t in iter_all_tasks(root) if t.get("type") == "USERTASK"]
    assert any("副总经理" in (name or "") for name in names)
    assert not any("张婷婷" in (name or "") for name in names)
    assert any("财务总监" in (name or "") for name in names)


def test_naming_rules_strip_person_and_merge_suffix():
    from core.workflow.guide_dag_compiler import (
        _merge_task_name,
        _task_name_from_content,
    )

    assert _task_name_from_content("野马集团财务副总经理 张婷婷") == "野马集团财务副总经理审批"
    assert "张婷婷" not in _task_name_from_content("野马集团财务副总经理 张婷婷")
    assert _task_name_from_content("总裁办主任") == "总裁办主任审批"
    assert _merge_task_name("总裁办主任") == "总裁办主任审批2"
    assert _merge_task_name("总裁办主任审批") == "总裁办主任审批2"


def test_shell_detection():
    shell_resp = {
        "code": 200,
        "data": {
            "wfSimpleTaskInfo": {
                "type": "STARTTASK",
                "taskName": "办理start",
                "child": {"type": "ROUTE", "conditions": [{}, {}]},
            }
        },
    }
    good_resp = {
        "code": 200,
        "data": {
            "wfSimpleTaskInfo": {
                "type": "STARTTASK",
                "taskName": "申请填报",
                "child": {"type": "ROUTE", "conditions": [{}] * 8},
            }
        },
    }
    assert response_has_task_tree(shell_resp)
    assert _response_looks_like_shell(shell_resp, min_route_branches=4)
    assert not _response_looks_like_shell(good_resp, min_route_branches=4)

    collapsed_resp = {
        "code": 200,
        "data": {
            "wfSimpleTaskInfo": {
                "type": "STARTTASK",
                "taskName": "申请填报",
                "taskKey": "sqtb",
                "child": None,
            }
        },
    }
    assert _response_looks_like_shell(collapsed_resp, min_route_branches=4)


def test_usertask_permissions_attached_on_first_finalize():
    from core.workflow.shenbi_builder import build_workflow_payloads

    model = build_workflow_payloads("野马集团二线招聘需求表")["model"]
    start = model["wfSimpleTaskInfo"]
    props = (start.get("properties") or {}).get("fromPropertyList") or []
    assert len(props) >= 10
    usertasks = [t for t in iter_all_tasks(model["wfSimpleTaskInfo"]) if t.get("type") == "USERTASK"]
    assert usertasks
    sample = usertasks[0]
    uprops = (sample.get("properties") or {}).get("fromPropertyList") or []
    assert len(uprops) >= 10
    writable = [fp for fp in uprops if fp.get("operating") == "writable"]
    assert len(writable) == 1
    assert writable[0]["fieldProp"] == sample["taskKey"]
    assert next(fp for fp in uprops if fp.get("fieldProp") == "orgId")["operating"] == "readable"
    other_approval = next(
        fp for fp in uprops
        if fp.get("fieldProp") not in {sample["taskKey"], "orgId", "processCode", "fqr", "zpbm", "zwjb"}
        and str(fp.get("fieldProp", "")).endswith("sp")
    )
    assert other_approval["operating"] == "readable"


def test_audit_branch_zpbm_conditions():
    import json

    root = compile_recruitment_from_guide(_recruitment_guide_nodes(), proc_id="p1", settings=_settings())
    branches = {
        t["taskKey"]: json.loads(
            t["properties"]["branchConditionList"][0]["conditionValue"]
        )
        for t in iter_all_tasks(root)
        if t.get("type") == "BRANCHTASK" and t.get("taskKey") in {"bssjb", "ssjb"}
    }
    assert branches["ssjb"] == [{"label": "集团审计部", "value": "6"}]
    assert {"label": "集团审计部", "value": "6"} not in branches["bssjb"]
    assert any(o["value"] == "1" for o in branches["bssjb"])


def test_cc_recipients_split_from_guide():
    root = compile_recruitment_from_guide(_recruitment_guide_nodes(), proc_id="p1", settings=_settings())
    cc_tasks = [t for t in iter_all_tasks(root) if t.get("type") == "CCTASK"]
    cc_names = [t["taskName"] for t in cc_tasks]
    assert "抄送野马集团招聘主管" in cc_names
    assert "抄送野马集团招聘专员" in cc_names
    assert not any("、" in (n or "") for n in cc_names)
    # 职级分支另有抄送总经理；副主任后仅 2 个抄送
    deputy_cc = []
    for t in cc_tasks:
        if t.get("taskName") in {"抄送野马集团招聘主管", "抄送野马集团招聘专员"}:
            deputy_cc.append(t)
    assert len(deputy_cc) == 2
    assert len(cc_tasks) == 3


def test_cctask_has_no_from_property_list():
    from core.workflow.shenbi_builder import build_workflow_payloads

    model = build_workflow_payloads("野马集团二线招聘需求表")["model"]
    from core.workflow.complex_flow_builder import iter_all_tasks

    for t in iter_all_tasks(model["wfSimpleTaskInfo"]):
        if t.get("type") == "CCTASK":
            props = t.get("properties") or {}
            assert props.get("fromPropertyList") is None
    from core.workflow.flow_semantics import split_cc_recipients

    assert split_cc_recipients("野马集团招聘主管、野马集团招聘专员") == [
        "野马集团招聘主管",
        "野马集团招聘专员",
    ]
    assert split_cc_recipients("A，B,C；D") == ["A", "B", "C", "D"]
