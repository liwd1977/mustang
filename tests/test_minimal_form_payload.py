"""新增流程最小表单 payload 测试。"""

from __future__ import annotations

import pytest

from core.config.settings import Settings, get_settings
from core.workflow.shenbi_builder import _minimal_form_record, build_template_workflow_payloads
from core.workflow.template_matcher import TemplateGenerationTarget
from schemas.workflow import WorkflowFlowResult, WorkflowImage, WorkflowNodeItem


@pytest.fixture
def builder_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _flow(title: str) -> WorkflowFlowResult:
    return WorkflowFlowResult(
        image=WorkflowImage(index=1, title_hint=title, filename="f.png"),
        title=title,
        success=True,
        nodes=[
            WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
            WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="审批", prev_seq=[1], next_seq=[]),
        ],
    )


def test_minimal_form_record_has_no_business_fields():
    rec = _minimal_form_record("测试流程")
    assert rec.success is True
    assert rec.fields == []


def test_build_payload_for_new_form(builder_settings: Settings):
    target = TemplateGenerationTarget(
        target_key="s1:3:集团印鉴使用申请单",
        guide_sector_index=2,
        guide_sector_title="野马集团",
        guide_title="集团印鉴使用申请单",
        workflow_key="集团印鉴使用申请单",
        form_slug="新增",
        app_display_name="AI_集团印鉴使用申请单",
        is_new_form=True,
        enabled=True,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.shenbi_client.load_workflow_registry", lambda *a, **k: None)
        payloads = build_template_workflow_payloads(
            target,
            settings=builder_settings,
            parsed_flow=_flow("集团印鉴使用申请单"),
        )

    assert payloads["is_new_form"] is True
    assert payloads["field_count"] == 0
    form_model = payloads["model"]["formModel"]
    info_group = next(g for g in form_model["group"] if g["label"] == "表单信息")
    labels = [c["label"] for c in info_group["column"] if c.get("display")]
    assert "发起人" in labels
    assert "部门" in labels
    assert "流程编号" in labels
