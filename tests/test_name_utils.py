"""工作流显示名与表单名规则测试。"""

from __future__ import annotations

from core.form.name_utils import display_app_name, display_form_name, display_workflow_name


def test_expense_reimbursement_display_names():
    assert display_form_name("集团费用报销单（已核准）") == "集团费用报销单"
    assert display_app_name("集团费用报销单（已核准）") == "集团费用报销单"
    assert display_workflow_name("集团费用报销单") == "集团费用报销单"
    assert display_app_name("集团费用报销单") != "集团费用"


def test_sample_car_and_recruitment_display_names():
    assert display_form_name("外贸集团样车合同备案表（已核准）") == "外贸集团样车合同备案表"
    assert display_app_name("外贸集团样车合同备案表（已核准）") == "外贸集团样车合同备案表"
    assert display_form_name("野马集团二线招聘需求表（已核对）") == "野马集团二线招聘需求表"
    assert display_app_name("野马集团二线招聘需求表（已核对）") == "野马集团二线招聘需求表"


def test_loan_application_display_name():
    assert display_app_name("外贸集团：布尔津矿业（领）借款单（已核准）") == "外贸集团：布尔津矿业（领）借款单"
    assert display_form_name("外贸集团：布尔津矿业（领）借款单") == "外贸集团：布尔津矿业（领）借款单"


def test_expense_payload_form_and_proc_names():
    from core.workflow.shenbi_builder import build_workflow_payloads, finalize_workflow_model

    model = finalize_workflow_model(build_workflow_payloads("集团费用报销单")["model"])
    assert model["formModel"]["tableComment"] == "集团费用报销单"
    assert model["wfSimpleProc"]["procName"] == "集团费用报销单"
