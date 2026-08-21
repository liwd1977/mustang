"""空壳环节树自动恢复测试。"""

from __future__ import annotations

from unittest.mock import patch

from core.workflow.complex_flow_builder import count_task_types
from core.workflow.flow_topology import local_task_tree_intact, min_route_branches_for_local
from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model, parsed_flow_matches_catalog
from core.workflow.shenbi_client import _apply_create_save_with_shell_recovery
from schemas.workflow import WorkflowFlowResult, WorkflowImage, WorkflowNodeItem


def test_linear_min_route_branches_zero():
    model = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    tree = model["wfSimpleTaskInfo"]
    assert int(count_task_types(tree).get("ROUTE") or 0) == 0
    assert min_route_branches_for_local(tree) == 0
    assert local_task_tree_intact(tree)


def test_parsed_flow_matches_catalog_rejects_recruitment_for_sample_car():
    flow = WorkflowFlowResult(
        image=WorkflowImage(title_hint="野马集团二线招聘需求表（已核对）"),
        title="野马集团二线招聘需求表（已核对）",
        success=True,
        nodes=[
            WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[]),
        ],
    )
    assert not parsed_flow_matches_catalog("外贸集团样车合同备案表", flow)
    assert parsed_flow_matches_catalog("外贸集团样车合同备案表", WorkflowFlowResult(
        image=WorkflowImage(title_hint="外贸集团样车合同备案表（已核准）"),
        title="外贸集团样车合同备案表（已核准）",
        success=True,
        nodes=flow.nodes,
    ))


def test_apply_create_save_keeps_local_tree_on_shell_response():
    local = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    local_types = count_task_types(local["wfSimpleTaskInfo"])
    shell_task = {
        "id": "t1",
        "type": "STARTTASK",
        "taskKey": "sqtb",
        "taskName": "办理start",
        "child": None,
    }
    shell_response = {
        "code": 200,
        "data": {
            "wfSimpleTaskInfo": shell_task,
            "formModel": '{"formId":"f1"}',
            "wfSimpleProc": '{"id":"p1","procKey":"cslc_x"}',
        },
    }

    results: list[dict] = []
    with patch("core.workflow.shenbi_client.save_proc_model", return_value=shell_response):
        merged, _resp = _apply_create_save_with_shell_recovery(
            local,
            local,
            local_tree=local["wfSimpleTaskInfo"],
            min_route_branches=0,
            settings=None,
            token="tok",
            results=results,
            recreate=False,
        )
    assert count_task_types(merged["wfSimpleTaskInfo"]) == local_types
    assert any(r.get("step") == "空壳恢复" for r in results)
