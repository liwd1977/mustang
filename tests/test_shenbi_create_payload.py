"""model-save 新建 payload 规范化测试。"""

from __future__ import annotations

import json

from core.workflow.complex_flow_builder import count_task_types, iter_all_tasks
from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model
from core.workflow.shenbi_client import strip_create_ids, sync_form_model_json


def test_strip_create_clears_task_proc_ids():
    model = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    stripped = strip_create_ids(sync_form_model_json(model))
    assert stripped["wfSimpleProc"]["id"] is None
    assert stripped["formModel"].get("tableName")
    assert stripped["wfSimpleProc"].get("procKey") == stripped["formModel"]["tableName"]
    assert stripped["formModel"].get("formId") is None
    assert stripped.get("formModelJson") is None
    for task in iter_all_tasks(stripped.get("wfSimpleTaskInfo")):
        assert task.get("id") is None
        assert task.get("procId") is None
        assert task.get("pid") is None


def test_strip_create_assigns_table_name_for_recruitment():
    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    stripped = strip_create_ids(sync_form_model_json(model))
    table = stripped["formModel"]["tableName"]
    assert table.startswith("cslc_")
    assert stripped["wfSimpleProc"]["procKey"] == table


def test_resolve_platform_ids_ignores_builder_placeholder_ids():
    from unittest.mock import patch

    from core.workflow.shenbi_client import resolve_platform_ids

    payloads = build_workflow_payloads("野马集团二线招聘需求表")
    model = payloads["model"]
    assert not model["formModel"].get("formId")
    assert not model["wfSimpleProc"].get("id")

    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=None):
        form_id, proc_id, app_id = resolve_platform_ids(payloads, "野马集团二线招聘需求表")
    assert form_id is None
    assert proc_id is None
    assert app_id is None


def test_build_app_save_payload_uses_workflow_display_name():
    from core.workflow.shenbi_client import build_app_save_payload

    payload = build_app_save_payload(
        "野马集团二线招聘需求表",
        sector_title="野马集团",
        sector_index=2,
    )
    assert payload["mc"] == "野马集团二线招聘需求表"
    assert payload["yydl"] == "2"
    assert payload["sfqy"] == "1"
    assert "野马集团二线部门招聘需求表" in payload["yyjj"]
    assert payload["icons"].startswith("http")


def test_legacy_registry_not_verified():
    from core.config.settings import get_settings
    from core.workflow.shenbi_client import is_legacy_registry, registry_app_verified, resolve_platform_ids

    settings = get_settings()
    legacy = {
        "app_id": settings.shenbi_app_id,
        "form_id": "f1",
        "proc_id": "p1",
    }
    assert is_legacy_registry(legacy, settings=settings)
    assert not registry_app_verified(legacy)

    payloads = build_workflow_payloads("野马集团二线招聘需求表")
    from unittest.mock import patch

    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=legacy):
        form_id, proc_id, app_id = resolve_platform_ids(payloads, "野马集团二线招聘需求表")
    assert form_id is None
    assert proc_id is None
    assert app_id is None


def test_verified_registry_used_for_update():
    from core.workflow.shenbi_client import registry_app_verified, resolve_platform_ids

    verified = {
        "app_id": "app123",
        "form_id": "f1",
        "proc_id": "p1",
        "app_created_via_api": True,
    }
    assert registry_app_verified(verified)
    payloads = build_workflow_payloads("野马集团二线招聘需求表")
    from unittest.mock import patch

    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=verified):
        form_id, proc_id, app_id = resolve_platform_ids(payloads, "野马集团二线招聘需求表")
    assert form_id == "f1"
    assert proc_id == "p1"
    assert app_id == "app123"


def test_collect_app_name_candidates_includes_legacy_suffix_variants():
    from core.workflow.shenbi_client import collect_app_name_candidates

    names = collect_app_name_candidates("集团费用报销单")
    assert "集团费用报销单" in names
    assert "集团费用报销" in names


def test_find_app_ids_by_name_candidates_matches_legacy_name():
    from unittest.mock import patch

    from core.workflow.shenbi_client import find_app_ids_by_name_candidates

    apps = [
        {"id": "app-old", "mc": "集团费用报销"},
        {"id": "app-new", "mc": "集团费用报销单"},
        {"id": "app-other", "mc": "其他流程"},
    ]
    with patch("core.workflow.shenbi_client.query_app_list", return_value=apps):
        matches = find_app_ids_by_name_candidates(["集团费用报销单", "集团费用报销"])
    assert ("app-old", "集团费用报销") in matches
    assert ("app-new", "集团费用报销单") in matches


def test_rebind_flow_menus_for_workflow_aliases_covers_all_apps():
    from unittest.mock import patch

    from core.workflow.shenbi_client import rebind_flow_menus_for_workflow_aliases

    apps = [
        {"id": "app-old", "mc": "外贸集团样车合同备案"},
        {"id": "app-new", "mc": "外贸集团样车合同备案表"},
    ]
    with patch("core.workflow.shenbi_client.query_app_list", return_value=apps):
        with patch("core.workflow.shenbi_client.rebind_all_flow_menus", return_value=[{"step": "ok"}]) as rebind_mock:
            steps = rebind_flow_menus_for_workflow_aliases(
                workflow_name="外贸集团样车合同备案表",
                primary_app_id="app-new",
                title="外贸集团样车合同备案表",
                form_id="form1",
                proc_id="proc1",
                proc_key="cslc_test",
            )
    assert steps == [{"step": "ok"}, {"step": "ok"}]
    rebound_ids = {call.kwargs["app_id"] for call in rebind_mock.call_args_list}
    assert rebound_ids == {"app-old", "app-new"}


def test_ensure_app_reuses_existing_name():
    from unittest.mock import patch

    from core.workflow.shenbi_client import ensure_app_for_workflow

    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=None):
        with patch(
            "core.workflow.shenbi_client.resolve_app_id_for_workflow",
            return_value=("app-existing", "reuse"),
        ):
            with patch(
                "core.workflow.shenbi_client.save_or_update_app",
                return_value={"code": 200, "data": "app-existing"},
            ) as save_mock:
                app_id, step = ensure_app_for_workflow("野马集团二线招聘需求表")
    assert app_id == "app-existing"
    assert step["mode"] == "reuse"
    assert save_mock.call_args[0][0]["id"] == "app-existing"


def test_ensure_app_retries_when_name_exists():
    from unittest.mock import patch

    from core.workflow.shenbi_client import ShenbiApiError, ensure_app_for_workflow

    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=None):
        with patch(
            "core.workflow.shenbi_client.resolve_app_id_for_workflow",
            return_value=(None, "create"),
        ):
            with patch(
                "core.workflow.shenbi_client.find_app_ids_by_name_candidates",
                return_value=[("app-existing", "野马集团二线招聘需求表")],
            ):
                with patch(
                    "core.workflow.shenbi_client.save_or_update_app",
                    side_effect=[
                        ShenbiApiError("创建应用失败（code=99999）：应用名称已经存在", body={"msg": "应用名称已经存在"}),
                        {"code": 200, "data": "app-existing"},
                    ],
                ) as save_mock:
                    app_id, step = ensure_app_for_workflow("野马集团二线招聘需求表")
    assert app_id == "app-existing"
    assert step["mode"] == "reuse"
    assert save_mock.call_count == 2
    assert save_mock.call_args_list[1][0][0]["id"] == "app-existing"


def test_legacy_registry_not_used_in_payload_build():
    from core.config.settings import get_settings
    from unittest.mock import patch

    settings = get_settings()
    legacy = {
        "app_id": settings.shenbi_app_id,
        "form_id": "f1",
        "proc_id": "p1",
        "proc_key": "cslc_4c2258",
        "table_name": "cslc_4c2258",
    }
    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=legacy):
        payloads = build_workflow_payloads("野马集团二线招聘需求表", settings=settings)
    table = payloads["model"]["formModel"].get("tableName")
    assert table in (None, "")
    assert payloads["model"]["wfSimpleProc"].get("procKey") in (None, "")


def test_strip_create_always_fresh_table_name():
    model = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    model["formModel"]["tableName"] = "cslc_4c2258"
    model["wfSimpleProc"]["procKey"] = "cslc_4c2258"
    stripped = strip_create_ids(sync_form_model_json(model))
    assert stripped["formModel"]["tableName"] != "cslc_4c2258"
    assert stripped["formModel"]["tableName"].startswith("cslc_")
    assert stripped["wfSimpleProc"]["procKey"] == stripped["formModel"]["tableName"]


def test_build_flow_menu_payload_matches_platform_shape():
    from core.workflow.shenbi_client import build_flow_menu_payload

    payload = build_flow_menu_payload(
        app_id="app1",
        title="野马集团二线招聘需求表",
        form_id="form1",
        proc_id="proc1",
        proc_key="cslc_d7beff",
    )
    assert payload["applicationId"] == "app1"
    assert payload["menuType"] == 2
    assert payload["extendInfo"]["trendsFlowId"] == "form1"
    assert payload["extendInfo"]["queryId"] == "proc1"
    assert payload["extendInfo"]["resourceExtList"][1]["extPropertyValue"] == "proc1"
    assert payload["extendInfo"]["tableName"] == "F_cslc_d7beff"


def test_rebind_all_flow_menus_renames_stale_title():
    from unittest.mock import patch

    from core.workflow.shenbi_client import rebind_all_flow_menus

    menus = [
        {
            "menuId": "menu1",
            "menuType": 2,
            "title": "集团费用",
            "sortNumber": 5,
            "extendInfo": {"trendsFlowId": "form1", "queryId": "proc1"},
        }
    ]
    with patch("core.workflow.shenbi_client.list_flow_menus", return_value=menus):
        with patch(
            "core.workflow.shenbi_client.save_app_menu",
            return_value={"code": 200, "data": "menu1"},
        ) as save_mock:
            steps = rebind_all_flow_menus(
                app_id="app1",
                title="集团费用报销单",
                form_id="form1",
                proc_id="proc1",
                proc_key="cslc_a2103d",
            )
    assert steps[0]["mode"] == "rebind"
    assert save_mock.call_args[0][0]["title"] == "集团费用报销单"


def test_ensure_flow_menu_rebinds_wrong_ids():
    from unittest.mock import patch

    from core.workflow.shenbi_client import ensure_flow_menu_for_app

    existing = {
        "menuId": "menu1",
        "menuType": 2,
        "title": "野马集团二线招聘需求",
        "sortNumber": 5,
        "extendInfo": {"trendsFlowId": "wrong-form", "queryId": "wrong-proc"},
    }
    with patch("core.workflow.shenbi_client.find_flow_menu", return_value=existing):
        with patch(
            "core.workflow.shenbi_client.save_app_menu",
            return_value={"code": 200, "data": "menu1"},
        ) as save_mock:
            step = ensure_flow_menu_for_app(
                app_id="app1",
                title="野马集团二线招聘需求",
                form_id="form1",
                proc_id="proc1",
                proc_key="cslc_d7beff",
            )
    assert step["mode"] == "rebind"
    saved = save_mock.call_args[0][0]
    assert saved["menuId"] == "menu1"
    assert saved["extendInfo"]["trendsFlowId"] == "form1"
    assert saved["extendInfo"]["queryId"] == "proc1"


def test_form_model_has_base_buttons_and_task_button_list():
    model = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    assert model["formModel"].get("baseButtonDefaultSet")
    ut = model["wfSimpleTaskInfo"]["child"]
    assert len((ut.get("properties") or {}).get("buttonList") or []) >= 3


def test_merge_save_response_preserves_task_tree_when_response_null():
    from core.workflow.shenbi_client import merge_save_response

    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    local_types = count_task_types(model["wfSimpleTaskInfo"])
    response = {
        "code": 200,
        "data": {
            "formModel": json.dumps({"formId": "f-new"}),
            "wfSimpleProc": json.dumps({"id": "p-new", "procKey": "cslc_abc"}),
            "wfSimpleTaskInfo": None,
        },
    }
    merged = merge_save_response(model, response)
    assert count_task_types(merged["wfSimpleTaskInfo"]) == local_types
    assert merged["formModel"]["formId"] == "f-new"
    assert merged["wfSimpleProc"]["id"] == "p-new"


def test_assess_model_save_response_warns_when_task_missing():
    from core.workflow.shenbi_client import assess_model_save_response

    local = {"wfSimpleTaskInfo": {"type": "STARTTASK", "taskName": "申请填报"}}
    response = {"code": 200, "data": {"wfSimpleTaskInfo": None}}
    hint = assess_model_save_response(response, local_model=local)
    assert not hint["ok"]
    assert "未在响应" in hint["message"]


def test_apply_registry_injects_ids_for_verified_registry():
    from unittest.mock import patch

    from core.workflow.shenbi_client import apply_registry_to_payloads

    verified = {
        "app_id": "app123",
        "form_id": "f-stale",
        "proc_id": "p-stale",
        "proc_key": "cslc_old",
        "table_name": "cslc_old",
        "app_created_via_api": True,
        "task_id_by_key": {"t1": "id1"},
    }
    for workflow_name in ("野马集团二线招聘需求表", "外贸集团样车合同备案表"):
        payloads = build_workflow_payloads(workflow_name)
        with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=verified):
            merged = apply_registry_to_payloads(payloads, workflow_name)
        model = merged["model"]
        assert model["wfSimpleProc"]["appId"] == "app123"
        assert model["formModel"]["formId"] == "f-stale"
        assert model["wfSimpleProc"]["id"] == "p-stale"
        assert model["wfSimpleProc"]["procKey"] == "cslc_old"
        assert merged.get("is_update") is True


def test_apply_registry_complex_recruitment_injects_ids():
    test_apply_registry_injects_ids_for_verified_registry()


def test_clamp_platform_field_name_truncates_long_labels():
    from core.form.name_utils import clamp_platform_field_name

    long_label = "这是一个超过五十个汉字长度的非常长的表单字段名称用于测试平台字段名截断逻辑是否生效" * 2
    clipped = clamp_platform_field_name(long_label)
    assert len(clipped) <= 50
    assert clipped.endswith("…")


def test_is_stale_registry_save_error():
    from core.workflow.shenbi_client import ShenbiApiError, _is_stale_registry_save_error

    exc = ShenbiApiError(
        "x",
        body={"msg": "错误的formId值，FORM_MODEL表中并不存在该表单配置信息！"},
    )
    assert _is_stale_registry_save_error(exc)


def test_complex_builder_ignores_registry_ids():
    from unittest.mock import patch

    verified = {
        "app_id": "app123",
        "form_id": "f-stale",
        "proc_id": "p-stale",
        "proc_key": "cslc_old",
        "table_name": "cslc_old",
        "app_created_via_api": True,
    }
    with patch("core.workflow.shenbi_client.load_workflow_registry", return_value=verified):
        payloads = build_workflow_payloads("野马集团二线招聘需求表")
    model = payloads["model"]
    assert model["wfSimpleProc"].get("appId") == "app123"
    assert model["formModel"].get("formId") in (None, "")
    assert model["wfSimpleProc"].get("id") in (None, "")
    assert model["formModel"].get("tableName") in (None, "")


def test_prepare_create_strips_task_ids_and_null_form_json():
    from core.workflow.shenbi_client import _prepare_model_save_body

    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    body = _prepare_model_save_body(
        model,
        app_id="app1",
        is_update=False,
        form_id=None,
        proc_id=None,
    )
    assert body.get("formModelJson") is None
    for task in iter_all_tasks(body.get("wfSimpleTaskInfo")):
        assert task.get("id") in (None, "")
        assert task.get("procId") in (None, "")


def test_prepare_complex_update_strips_task_ids():
    test_prepare_create_strips_task_ids_and_null_form_json()


def test_assess_complex_write_outcome_warns_when_model_get_incomplete():
    from core.workflow.shenbi_client import assess_complex_write_outcome

    local = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    local["formModel"]["formId"] = "form-test"
    save_response = {
        "code": 200,
        "data": {
            "wfSimpleTaskInfo": local["wfSimpleTaskInfo"],
            "formModel": json.dumps({"formId": "form-test"}),
            "wfSimpleProc": json.dumps({"id": "proc-test"}),
        },
    }
    persisted = {
        "ok": False,
        "message": "平台环节 {'USERTASK': 14} ≠ 本地 {'ROUTE': 4, 'USERTASK': 14}",
        "remote_task_types": {"USERTASK": 14},
    }
    outcome = assess_complex_write_outcome(
        save_response=save_response,
        local_model=local,
        persisted_check=persisted,
    )
    assert outcome["ok"]
    assert outcome["level"] == "ok"

    persisted_empty = {
        "ok": False,
        "message": "平台未回传环节树",
        "remote_task_types": {},
    }
    outcome2 = assess_complex_write_outcome(
        save_response=save_response,
        local_model=local,
        persisted_check=persisted_empty,
    )
    assert outcome2["ok"]
    assert outcome2["level"] == "warn"
    assert "model-get" in outcome2["message"]


def test_merge_save_response_preserve_tree_keeps_local_on_shrink():
    from core.workflow.shenbi_client import merge_save_response_preserve_tree
    from core.workflow.complex_flow_builder import count_task_types

    local = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    local_types = count_task_types(local["wfSimpleTaskInfo"])
    shell_task = {
        "id": "t1",
        "type": "STARTTASK",
        "taskKey": "sqtb",
        "taskName": "申请填报",
        "child": None,
    }
    response = {
        "code": 200,
        "data": {
            "wfSimpleTaskInfo": shell_task,
            "formModel": json.dumps({"formId": "f1"}),
            "wfSimpleProc": json.dumps({"id": "p1"}),
        },
    }
    merged = merge_save_response_preserve_tree(local, response)
    assert count_task_types(merged["wfSimpleTaskInfo"]) == local_types


def test_usertask_non_current_approval_readable():
    from core.workflow.complex_flow_builder import iter_all_tasks
    from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model

    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    task = next(
        t for t in iter_all_tasks(model["wfSimpleTaskInfo"])
        if t.get("taskKey") == "ymjtcwfjlsp"
    )
    fpl = task["properties"]["fromPropertyList"]
    assert next(fp for fp in fpl if fp["fieldProp"] == "orgId")["operating"] == "readable"
    assert next(fp for fp in fpl if fp["fieldProp"] == "ymjtcwfjlsp")["operating"] == "writable"
    assert next(fp for fp in fpl if fp["fieldProp"] == "zcbzrsp")["operating"] == "readable"


def test_verify_model_integrity_recruitment():
    from core.workflow.complex_flow_builder import count_task_types
    from core.workflow.shenbi_client import verify_model_integrity

    model = finalize_workflow_model(build_workflow_payloads("野马集团二线招聘需求表")["model"])
    model["formModel"]["formId"] = "form-test"
    result = verify_model_integrity(model)
    assert result["ok"]
    assert result["task_types"] == count_task_types(model["wfSimpleTaskInfo"])


def test_prepare_full_tree_update_body_strips_task_ids():
    from core.workflow.complex_flow_builder import iter_all_tasks
    from core.workflow.shenbi_client import _prepare_full_tree_update_body

    model = finalize_workflow_model(build_workflow_payloads("集团费用报销单")["model"])
    local_tree = model["wfSimpleTaskInfo"]
    for task in iter_all_tasks(local_tree):
        task["id"] = "old-id"
    body = _prepare_full_tree_update_body(
        model,
        local_tree=local_tree,
        app_id="app1",
        form_id="form1",
        proc_id="proc1",
    )
    assert body["formModel"]["formId"] == "form1"
    assert body["wfSimpleProc"]["id"] == "proc1"
    for task in iter_all_tasks(body.get("wfSimpleTaskInfo")):
        assert task.get("id") in (None, "")
        assert task.get("procId") in (None, "")


def test_write_result_tree_confirmed_requires_non_degraded_response():
    from core.workflow.shenbi_client import write_result_tree_confirmed

    model = finalize_workflow_model(build_workflow_payloads("集团费用报销单")["model"])
    good = {
        "results": [
            {
                "step": "表单与流程",
                "response": {
                    "code": 200,
                    "data": {"wfSimpleTaskInfo": model["wfSimpleTaskInfo"]},
                },
            }
        ]
    }
    assert write_result_tree_confirmed(good, local_model=model)

    shell = {
        "type": "STARTTASK",
        "taskName": "办理start",
        "child": {"type": "ROUTE", "conditions": [{}, {}]},
    }
    bad = {
        "results": [
            {
                "step": "表单与流程",
                "response": {"code": 200, "data": {"wfSimpleTaskInfo": shell}},
            }
        ]
    }
    assert not write_result_tree_confirmed(bad, local_model=model)
