"""表单操作权限规范化单元测试。"""

from __future__ import annotations

from core.workflow.shenbi_builder import (
    _attach_form_permissions,
    _build_form_model_shell,
    _normalize_form_permissions,
)


def test_starttask_approval_fields_readable_not_hide():
    form_model = _build_form_model_shell(
        form_info_cols=[
            {"label": "部门", "prop": "orgId", "display": True},
            {"label": "采购合同号", "prop": "cghtbh", "display": True},
        ],
        approval_cols=[
            {"label": "部门经理审批意见", "prop": "todo", "display": True},
        ],
        form_id="f1",
        workflow_name="测试流程",
        table_name="tbl1",
    )
    model = {
        "formModel": form_model,
        "wfSimpleTaskInfo": {
            "id": "t1",
            "type": "STARTTASK",
            "taskKey": "sqtb",
            "properties": {
                "fromPropertyList": [
                    {"fieldProp": "todo", "operating": "hide", "taskId": "t1"},
                ]
            },
        },
    }
    _attach_form_permissions(model)
    fixed = _normalize_form_permissions(model)
    start = fixed["wfSimpleTaskInfo"]
    todo = next(fp for fp in start["properties"]["fromPropertyList"] if fp["fieldProp"] == "todo")
    assert todo["operating"] == "readable"
