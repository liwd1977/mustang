"""resolve_parsed_flow 开发模式按需解析测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.config.settings import get_settings
from schemas.workflow import WorkflowFlowResult, WorkflowImage, WorkflowNodeItem


@pytest.fixture
def dev_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("GUIDE_PARSE_USE_CACHE", "0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _sample_flow() -> WorkflowFlowResult:
    return WorkflowFlowResult(
        image=WorkflowImage(index=4, filename="flow_06.png", title_hint="野马集团二线招聘需求表（已核对）"),
        title="野马集团二线招聘需求表（已核对）",
        success=True,
        nodes=[
            WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
            WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="发起人", prev_seq=[1], next_seq=[]),
        ],
    )


def test_resolve_parsed_flow_uses_cache_when_present(dev_settings):
    from core.workflow import result_store
    from schemas.workflow import GuideParseResult

    flow = _sample_flow()
    cached = GuideParseResult(source_file="guide.docx", parse_finished_at="2026-08-15T12:00:00")
    with patch.object(result_store, "load_result", return_value=cached), patch(
        "core.workflow.shenbi_builder.find_parsed_flow", return_value=flow
    ), patch("core.workflow.pipeline.recognize_catalog_workflow") as recognize:
        resolved = result_store.resolve_parsed_flow("野马集团二线招聘需求表", settings=dev_settings)
        assert resolved is flow
        recognize.assert_not_called()


def test_resolve_parsed_flow_triggers_targeted_vlm_when_missing(dev_settings):
    from core.workflow import result_store

    flow = _sample_flow()
    with patch("core.workflow.result_store.load_result", return_value=None), patch(
        "core.workflow.shenbi_builder.find_parsed_flow", return_value=None
    ), patch("core.workflow.pipeline.recognize_catalog_workflow", return_value=flow) as recognize:
        resolved = result_store.resolve_parsed_flow("野马集团二线招聘需求表", settings=dev_settings)
        assert resolved is flow
        recognize.assert_called_once()
