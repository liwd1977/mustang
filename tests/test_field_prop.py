"""表单字段 prop 命名单元测试。"""

from __future__ import annotations

import re

from core.config.settings import get_settings
from core.form.name_utils import is_valid_ascii_prop, task_key_from_name
from core.workflow.complex_flow_builder import _FlowCtx
from core.workflow.shenbi_builder import (
    _build_approval_columns,
    _ensure_task_keys,
    build_workflow_payloads,
    finalize_workflow_model,
    label_to_prop,
)

_VALID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def test_label_to_prop_rejects_chinese_and_numeric_labels():
    used: set[str] = set()
    assert label_to_prop("需求原因", used) == "xqyy"
    assert label_to_prop("数字1", used).startswith("f")
    assert _VALID.match(label_to_prop("数字1", set()))
    assert _VALID.match(label_to_prop("采购合同号", set()))


def test_task_key_from_name_rejects_digit_only_residue():
    name = "野马集团财务总监，野马集团财务副经理（2人）审批"
    key = task_key_from_name(name, index=5)
    assert is_valid_ascii_prop(key)
    assert not key[0].isdigit()


def test_approval_columns_use_valid_props_for_multi_role_nodes():
    settings = get_settings()
    ctx = _FlowCtx(proc_id="proc-test", settings=settings)
    root = ctx.start(task_name="申请填报")
    node = ctx.usertask_from_guide(
        root["id"],
        "野马集团财务总监，野马集团财务副经理（2人）审批",
        index=5,
    )
    root["child"] = node
    root = _ensure_task_keys(root)
    cols = _build_approval_columns(root)
    assert cols
    assert all(is_valid_ascii_prop(str(c["prop"])) for c in cols)
    model = {
        "formModel": {
            "group": [
                {"label": "表单信息", "column": []},
                {"label": "审批意见", "column": [{"prop": "2_5", "label": "bad"}]},
            ]
        },
        "wfSimpleTaskInfo": root,
    }
    finalized = finalize_workflow_model(model)
    approval = next(g for g in finalized["formModel"]["group"] if g["label"] == "审批意见")
    assert all(is_valid_ascii_prop(str(c["prop"])) for c in approval["column"])


def test_recruitment_payload_has_no_junk_props():
    payloads = build_workflow_payloads("野马集团二线招聘需求表")
    form_group = next(g for g in payloads["model"]["formModel"]["group"] if g["label"] == "表单信息")
    for col in form_group["column"]:
        prop = col.get("prop")
        label = col.get("label")
        assert prop and _VALID.match(str(prop)), f"invalid prop {prop!r} for label {label!r}"
        assert label != "数字1"


def test_textarea_fields_use_full_row_span():
    payloads = build_workflow_payloads("野马集团二线招聘需求表")
    form_group = next(g for g in payloads["model"]["formModel"]["group"] if g["label"] == "表单信息")
    by_label = {c["label"]: c for c in form_group["column"]}
    assert by_label["需求原因"]["type"] == "textarea"
    assert by_label["需求原因"]["span"] == 24
    assert by_label["职务级别"]["type"] == "select"
    assert by_label["职务级别"]["span"] == 12
