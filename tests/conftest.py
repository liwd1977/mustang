"""pytest 全局 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.settings import get_settings
from schemas.form import RawFormRecord, RawFormField
from schemas.workflow import WorkflowFlowResult, WorkflowImage, WorkflowNodeItem


def _seed_form_if_missing(template_name: str, fields: list[RawFormField]) -> None:
    from core.form.form_store import load_form, save_form

    settings = get_settings()
    if load_form(template_name, settings) is not None:
        return
    save_form(
        RawFormRecord(
            template_name=template_name,
            success=True,
            fields=fields,
        ),
        settings=settings,
    )


@pytest.fixture(scope="session", autouse=True)
def _seed_catalog_forms():
    _seed_form_if_missing(
        "外贸集团样车合同备案表",
        [
            RawFormField(label="采购合同号", input_type="input"),
            RawFormField(label="采购数量", input_type="number"),
        ],
    )
    _seed_form_if_missing(
        "野马集团二线部门招聘需求表",
        [
            RawFormField(label="招聘部门", input_type="select"),
            RawFormField(label="岗位名称", input_type="input"),
            RawFormField(label="需求原因", input_type="textarea"),
        ],
    )
    _seed_form_if_missing(
        "集团公司部门费用报销单",
        [
            RawFormField(label="费用事由", input_type="select"),
            RawFormField(label="报销金额", input_type="number"),
            RawFormField(label="收款单位", input_type="input"),
        ],
    )
    yield


def _load_expense_nodes_from_cache() -> list[WorkflowNodeItem] | None:
    parse_path = Path("output/workflows/guide_2026080301_parse_result.json")
    if not parse_path.is_file():
        return None
    import json

    data = json.loads(parse_path.read_text(encoding="utf-8"))
    for sr in data.get("sector_results") or []:
        for flow in sr.get("flows") or []:
            if flow.get("title") == "集团费用报销单（已核准）" and flow.get("nodes"):
                return [WorkflowNodeItem.model_validate(n) for n in flow["nodes"]]
    return None


def _expense_flow_result() -> WorkflowFlowResult:
    nodes = _load_expense_nodes_from_cache()
    if not nodes:
        raise ValueError("缺少费用报销 parse 缓存")
    return WorkflowFlowResult(
        image=WorkflowImage(
            index=1,
            filename="flow_01.png",
            title_hint="集团费用报销单（已核准）",
        ),
        title="集团费用报销单（已核准）",
        success=True,
        confidence=0.95,
        nodes=nodes,
    )


def _recruitment_flow_result() -> WorkflowFlowResult:
    from tests.test_guide_dag_compiler import _recruitment_guide_nodes

    return WorkflowFlowResult(
        image=WorkflowImage(
            index=4,
            filename="flow_06.png",
            title_hint="野马集团二线招聘需求表（已核对）",
        ),
        title="野马集团二线招聘需求表（已核对）",
        success=True,
        confidence=0.95,
        nodes=_recruitment_guide_nodes(),
    )


def _sample_car_flow_result() -> WorkflowFlowResult:
    nodes = [
        WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="提交人", prev_seq=[1], next_seq=[3]),
        WorkflowNodeItem(
            seq=3,
            shape="长方形",
            node_type="处理节点",
            content="发起人自选部门领导",
            prev_seq=[2],
            next_seq=[4],
        ),
        WorkflowNodeItem(
            seq=4,
            shape="长方形",
            node_type="处理节点",
            content="发起人自选会计",
            prev_seq=[3],
            next_seq=[5],
        ),
        WorkflowNodeItem(
            seq=5,
            shape="波形",
            node_type="抄送节点",
            content="抄送",
            prev_seq=[4],
            next_seq=[],
        ),
    ]
    return WorkflowFlowResult(
        image=WorkflowImage(index=1, filename="flow_01.png", title_hint="外贸集团样车合同备案表（已核准）"),
        title="外贸集团样车合同备案表（已核准）",
        success=True,
        confidence=0.95,
        nodes=nodes,
    )


def _fake_recognize_catalog_workflow(workflow_name: str, *, settings=None) -> WorkflowFlowResult:
    del settings
    if "招聘" in workflow_name:
        return _recruitment_flow_result()
    if "样车" in workflow_name:
        return _sample_car_flow_result()
    if "费用报销" in workflow_name:
        return _expense_flow_result()
    raise ValueError(f"测试未 stub 的流程：{workflow_name}")


@pytest.fixture(autouse=True)
def _guide_parse_for_tests(monkeypatch: pytest.MonkeyPatch):
    """测试走开发模式；缺省 parse 时用 stub 节点，避免依赖完整 VLM 缓存。"""
    monkeypatch.setenv("GUIDE_PARSE_USE_CACHE", "0")
    monkeypatch.setattr(
        "core.workflow.result_store.is_result_stale",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "core.workflow.pipeline.recognize_catalog_workflow",
        _fake_recognize_catalog_workflow,
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
