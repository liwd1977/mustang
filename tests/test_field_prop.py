"""表单字段 prop 命名单元测试。"""

from __future__ import annotations

import re

from core.workflow.shenbi_builder import build_workflow_payloads, label_to_prop

_VALID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def test_label_to_prop_rejects_chinese_and_numeric_labels():
    used: set[str] = set()
    assert label_to_prop("需求原因", used) == "xqyy"
    assert label_to_prop("数字1", used).startswith("f")
    assert _VALID.match(label_to_prop("数字1", set()))
    assert _VALID.match(label_to_prop("采购合同号", set()))


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
