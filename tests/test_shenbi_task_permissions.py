"""环节 id 与表单权限绑定测试。"""

from __future__ import annotations

from core.workflow.complex_flow_builder import iter_all_tasks
from core.workflow.shenbi_client import (
    apply_task_id_by_key,
    extract_task_id_by_key,
    merge_platform_task_ids,
    merge_platform_task_tree_inplace,
    prepare_from_property_list_for_platform_save,
    sync_from_property_list_task_ids,
    _prepare_model_save_body,
    _prepare_permission_patch_body,
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
    )
    kept = next(t for t in iter_all_tasks(body["wfSimpleTaskInfo"]) if t.get("taskKey") == "zcbzrsp")
    assert kept["id"] is None


def test_prepare_from_property_list_for_platform_save():
    root = {
        "id": "task-1",
        "type": "STARTTASK",
        "taskKey": "sqtb",
        "properties": {
            "fromPropertyList": [
                {
                    "id": "gen-id",
                    "taskId": None,
                    "fieldProp": "id",
                    "operating": "hide",
                    "creator": "wipadmin",
                },
                {
                    "id": "gen-id-2",
                    "taskId": None,
                    "fieldProp": "zpbm",
                    "operating": "writable",
                    "creator": "wipadmin",
                },
            ]
        },
    }
    prepare_from_property_list_for_platform_save(root)
    fpl = root["properties"]["fromPropertyList"]
    assert len(fpl) == 1
    assert fpl[0]["fieldProp"] == "zpbm"
    assert fpl[0]["taskId"] == "task-1"
    assert fpl[0]["id"] is None
    assert fpl[0]["creator"] is None


def test_permission_patch_body_uses_null_form_model_json():
    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    body = _prepare_permission_patch_body(
        model,
        platform_tree=None,
        app_id="app1",
        form_id="form1",
        proc_id="proc1",
    )
    assert body.get("formModelJson") is None
    start = body["wfSimpleTaskInfo"]
    fpl = start["properties"]["fromPropertyList"]
    assert any(fp.get("fieldProp") == "zpbm" and fp.get("operating") == "writable" for fp in fpl)


def test_merge_platform_task_tree_inplace_copies_route_id():
    local = {
        "type": "STARTTASK",
        "taskKey": "sqtb",
        "id": None,
        "child": {"type": "ROUTE", "id": None, "child": None, "conditions": []},
    }
    plat = {
        "type": "STARTTASK",
        "taskKey": "sqtb",
        "id": "start-plat",
        "child": {"type": "ROUTE", "id": "route-plat", "child": None, "conditions": []},
    }
    merge_platform_task_tree_inplace(local, plat)
    assert local["id"] == "start-plat"
    assert local["child"]["id"] == "route-plat"
