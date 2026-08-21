"""复杂流程组装单元测试。"""

from __future__ import annotations

from core.workflow.complex_flow_builder import (
    build_recruitment_task_tree,
    count_task_types,
    iter_all_tasks,
)
from core.workflow.shenbi_builder import WORKFLOW_CATALOG, build_workflow_payloads


def test_recruitment_task_tree_topology():
    settings = type("S", (), {"shenbi_tenant_id": "t1"})()
    root = build_recruitment_task_tree(proc_id="proc1", settings=settings)
    types = count_task_types(root)

    assert types["STARTTASK"] == 1
    assert types["ROUTE"] == 4
    assert types["USERTASK"] == 15
    assert types["CCTASK"] == 3
    assert types["BRANCHTASK"] == 12

    tasks = iter_all_tasks(root)
    assert root["type"] == "STARTTASK"
    assert root["child"]["type"] == "ROUTE"
    assert len(root["child"].get("conditions") or []) == 8
    assert root["child"]["child"]["taskKey"] == "zcbzrsp2"

    branch_keys = {c.get("taskKey") for c in root["child"]["conditions"] or []}
    assert branch_keys == {
        "jtdwbgsjtghdb",
        "zcb",
        "db",
        "jtcwb",
        "jtcgb",
        "jtjjb",
        "jtsjb",
        "jtgh",
    }


def test_recruitment_payload_has_branch_fields():
    from core.config.settings import get_settings
    from core.config.shenbi_environments import get_shenbi_config

    expected_role = get_shenbi_config(get_settings()).admin_role_code
    payloads = build_workflow_payloads("野马集团二线招聘需求表")
    form_model = payloads["model"]["formModel"]
    form_group = next(g for g in form_model["group"] if g["label"] == "表单信息")
    props = {c["prop"] for c in form_group["column"]}
    assert "zpbm" in props
    assert "zwjb" in props

    approval_group = next(g for g in form_model["group"] if g["label"] == "审批意见")
    assert len(approval_group["column"]) >= 10

    task_types = count_task_types(payloads["model"]["wfSimpleTaskInfo"])
    assert task_types["ROUTE"] == 4

    root = payloads["model"]["wfSimpleTaskInfo"]
    assert root["taskName"] == "申请填报"
    for task in iter_all_tasks(root):
        if task.get("type") not in {"USERTASK", "CCTASK"}:
            continue
        person_list = (task.get("properties") or {}).get("personList") or []
        assert person_list, f"{task.get('taskName')} 缺少 personList"
        assert person_list[0].get("personType") == "role"
        assert expected_role in (person_list[0].get("roleInfo") or "")
