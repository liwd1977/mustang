"""应用可见性校验测试。"""

from __future__ import annotations

from unittest.mock import patch

from core.workflow.shenbi_client import app_exists_on_platform, resolve_app_id_for_workflow


def test_resolve_app_id_ignores_stale_registry():
    registry = {"app_id": "ghost-app-id", "app_created_via_api": True, "app_mc": "AI_测试"}
    apps = [{"id": "real-app-id", "mc": "AI_测试"}]

    with patch("core.workflow.shenbi_client.query_app_list", return_value=apps):
        app_id, mode = resolve_app_id_for_workflow("wf", registry=registry)
    assert app_id == "real-app-id"
    assert mode == "reuse"


def test_model_conversion_error_detection():
    from core.workflow.shenbi_client import _is_model_conversion_error, ShenbiApiError

    exc = ShenbiApiError("x", body={"code": 13, "msg": "模型转换失败！"})
    assert _is_model_conversion_error(exc)


def test_save_with_wrong_tenant_normalizes():
    from unittest.mock import patch

    from core.config.settings import reload_settings
    from core.workflow.template_matcher import build_template_match_report, template_registry_name
    from core.workflow.shenbi_builder import build_template_workflow_payloads
    from core.workflow.shenbi_client import save_workflow_models

    ym_settings = reload_settings()
    wrong = ym_settings.model_copy(update={"shenbi_tenant_id": "aa4f8c3fa79111eda3250242ac120002"})
    report = build_template_match_report(settings=ym_settings)
    target = next(t for t in report.enabled_targets if t.guide_title == "外贸印章用印申请单")
    key = template_registry_name(target.target_key)
    payloads = build_template_workflow_payloads(target, settings=ym_settings)
    with patch("core.workflow.shenbi_client.query_app_list", return_value=[]):
        with patch(
            "core.workflow.shenbi_client.save_or_update_app",
            return_value={"code": 200, "data": "new-app-id"},
        ):
            with patch("core.workflow.shenbi_client.save_proc_model") as save_mock:
                save_mock.return_value = {
                    "code": 200,
                    "data": {
                        "formModel": {"formId": "f1"},
                        "wfSimpleProc": {"id": "p1", "procKey": "cslc_test01"},
                        "wfSimpleTaskInfo": {"type": "STARTTASK", "taskName": "申请填报"},
                    },
                }
                with patch("core.workflow.shenbi_client.publish_workflow", return_value={"ok": True, "message": "ok"}):
                    with patch("core.workflow.shenbi_client.verify_runtime_proc", return_value={"ok": True, "message": "ok"}):
                        with patch("core.workflow.shenbi_client.rebind_flow_menus_for_workflow_aliases", return_value=[]):
                            with patch("core.workflow.shenbi_client.verify_persisted_model", return_value={"ok": True, "message": "ok"}):
                                result = save_workflow_models(
                                    payloads,
                                    workflow_name=key,
                                    settings=wrong,
                                    template_mode=True,
                                    app_mc=target.app_display_name,
                                    form_slug=target.form_slug,
                                    sector_title=target.guide_sector_title,
                                    sector_index=target.guide_sector_index,
                                    recreate=True,
                                )
    assert result.get("app_id")


