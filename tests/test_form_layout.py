"""表单布局：避免 flowUserChoose 与常规字段混排导致空白/错位。"""

from __future__ import annotations

from core.workflow.shenbi_builder import (
    _balance_half_row_spans,
    _normalize_form_info_layout,
    build_workflow_payloads,
    finalize_workflow_model,
)


def test_normalize_moves_flow_user_choose_to_end():
    cols = [
        {"display": False, "prop": "id", "span": 12},
        {"display": True, "label": "部门", "type": "select", "span": 12},
        {"display": True, "label": "发起人", "type": "input", "span": 12},
        {"display": True, "label": "部门经理", "type": "flowUserChoose", "span": 12},
        {"display": True, "label": "采购合同号", "type": "input", "span": 12},
    ]
    _normalize_form_info_layout(cols)
    visible = [c for c in cols if c.get("display") is not False]
    assert [c.get("label") for c in visible] == ["部门", "发起人", "采购合同号", "部门经理"]
    assert visible[-1]["type"] == "flowUserChoose"


def test_balance_half_row_spans_expands_last_odd_field():
    cols = [
        {"label": "A", "span": 12},
        {"label": "B", "span": 12},
        {"label": "C", "span": 12},
    ]
    _balance_half_row_spans(cols)
    assert cols[0]["span"] == 12
    assert cols[1]["span"] == 12
    assert cols[2]["span"] == 24


def test_sample_car_form_has_no_mixed_user_choose_row():
    model = finalize_workflow_model(build_workflow_payloads("外贸集团样车合同备案表")["model"])
    visible = [
        c
        for c in model["formModel"]["group"][0]["column"]
        if c.get("display") is not False
    ]
    choose_indices = [i for i, c in enumerate(visible) if c.get("type") == "flowUserChoose"]
    if not choose_indices:
        return
    first_choose = choose_indices[0]
    for col in visible[:first_choose]:
        assert col.get("type") != "flowUserChoose"
    for i in range(len(choose_indices) - 1):
        assert choose_indices[i + 1] - choose_indices[i] == 1
