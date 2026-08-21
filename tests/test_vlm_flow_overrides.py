"""VLM 流程标题映射测试。"""

from __future__ import annotations

import pytest

from core.workflow.template_matcher import (
    VLM_FLOW_TITLE_OVERRIDES,
    find_parsed_flow_by_guide,
    resolve_vlm_lookup_titles,
)
from schemas.workflow import GuideParseResult, SectorBlock, SectorWorkflowResult, WorkflowFlowResult, WorkflowImage, WorkflowNodeItem


def _flow(title: str) -> WorkflowFlowResult:
    return WorkflowFlowResult(
        image=WorkflowImage(index=1, title_hint=title, filename="f.png"),
        title=title,
        success=True,
        nodes=[
            WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
        ],
    )


def test_vlm_override_mapping_contains_user_pairs():
    assert (
        VLM_FLOW_TITLE_OVERRIDES["外贸集团：布尔津矿业费用报销单"]
        == "外贸集团：布尔津矿业费用报销（支付）单"
    )
    assert VLM_FLOW_TITLE_OVERRIDES["文旅集团文化公司费用借款单"] == "文旅集团费用借款单"


def test_resolve_vlm_lookup_titles_includes_override():
    titles = resolve_vlm_lookup_titles("文旅集团文化公司费用报销单")
    assert "文旅集团费用报销单" in titles


def test_find_parsed_flow_uses_override_exact_match():
    sector = SectorBlock(index=4, title="四、文旅", text_content="", images=[])
    parse_result = GuideParseResult(
        source_file="g.docx",
        sectors=[sector],
        sector_results=[
            SectorWorkflowResult(
                sector=sector,
                flows=[_flow("文旅集团费用支付单（已核准）")],
            )
        ],
    )
    found = find_parsed_flow_by_guide(
        "文旅集团文化公司费用支付单",
        parse_result=parse_result,
    )
    assert found is not None
    assert "费用支付单" in (found.title or found.image.title_hint)
