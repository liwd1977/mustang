"""环节 id 与表单权限绑定测试。"""

from __future__ import annotations

from core.workflow.complex_flow_builder import iter_all_tasks
from core.workflow.shenbi_client import (
    apply_task_id_by_key,
    extract_task_id_by_key,
    merge_platform_task_ids,
    sync_from_property_list_task_ids,
    _prepare_model_save_body,
)
from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model


def test_merge_platform_task_ids_syncs_from_property_list():
    local = {
        "id": "local1",
        "type": "USERTASK",
        "taskKey": "zcbzrsp",
        "taskName": "总裁办主任审批",
        "properties": {
            "fromPropertyList": [
                {"fieldProp": "zcbzrsp", "operating": "writable", "taskId": "local1"},
            ]
        },
    }
    platform = {
        "id": "plat1",
        "type": "USERTASK",
        "taskKey": "zcbzrsp",
        "taskName": "总裁办主任审批",
        "properties": {"fromPropertyList": None},
    }
    merge_platform_task_ids(local, platform)
    assert local["id"] == "plat1"
    assert local["properties"]["fromPropertyList"][0]["taskId"] == "plat1"


def test_registry_task_id_roundtrip():
    model = build_workflow_payloads("野马集团二线招聘需求表")["model"]
    task_root = model["wfSimpleTaskInfo"]
    mapping = {"zcbzrsp": "platform-task-001", "ymjtrsjlsp": "platform-task-002"}
    apply_task_id_by_key(task_root, mapping)
    sync_from_property_list_task_ids(task_root)
    zcb = next(t for t in iter_all_tasks(task_root) if t.get("taskKey") == "zcbzrsp")
    assert zcb["id"] == "platform-task-001"
    fpl = zcb["properties"]["fromPropertyList"]
    assert any(fp["fieldProp"] == "zcbzrsp" and fp["taskId"] == "platform-task-001" for fp in fpl)
    assert extract_task_id_by_key(task_root)["zcbzrsp"] == "platform-task-001"


def test_update_save_body_keeps_task_ids():
    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    apply_task_id_by_key(model["wfSimpleTaskInfo"], {"zcbzrsp": "keep-id-1"})
    body = _prepare_model_save_body(
        model,
        app_id="app1",
        is_update=True,
        form_id="form1",
        proc_id="proc1",
        flow_kind="complex_recruitment",
    )
    kept = next(t for t in iter_all_tasks(body["wfSimpleTaskInfo"]) if t.get("taskKey") == "zcbzrsp")
    assert kept["id"] == "keep-id-1"
    fpl = kept["properties"]["fromPropertyList"]
    assert any(fp.get("taskId") == "keep-id-1" for fp in fpl)


def test_create_save_body_strips_task_ids():
    model = build_workflow_payloads("野马集团二线招聘需求表")["model"]
    body = _prepare_model_save_body(
        model,
        app_id="app1",
        is_update=False,
        form_id=None,
        proc_id=None,
        flow_kind="complex_recruitment",
    )
    kept = next(t for t in iter_all_tasks(body["wfSimpleTaskInfo"]) if t.get("taskKey") == "zcbzrsp")
    assert kept["id"] is None
