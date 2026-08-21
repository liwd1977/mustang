"""template_matcher 单元测试。"""

from __future__ import annotations

import pytest

from core.config.settings import Settings, get_settings
from core.form.template_table import TemplateTableRow
from core.workflow.template_matcher import (
    ai_app_display_name,
    build_template_match_report,
    flow_match_hints,
    make_row_target_key,
    make_target_key,
    resolve_row_oa_forms,
)
from schemas.workflow import GuideParseResult, SectorBlock, SectorWorkflowResult, WorkflowFlowResult, WorkflowImage, WorkflowNodeItem


@pytest.fixture
def matcher_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _image(title: str, *, index: int = 1) -> WorkflowImage:
    return WorkflowImage(index=index, title_hint=title, filename=f"flow_{index:02d}.png")


def _flow(title: str, *, index: int = 1) -> WorkflowFlowResult:
    return WorkflowFlowResult(
        image=_image(title, index=index),
        title=title,
        success=True,
        nodes=[
            WorkflowNodeItem(seq=1, shape="圆角矩形", node_type="开始节点", content="开始", prev_seq=[], next_seq=[2]),
            WorkflowNodeItem(seq=2, shape="长方形", node_type="处理节点", content="审批", prev_seq=[1], next_seq=[]),
        ],
    )


def _sector(index: int, title: str, flow_titles: list[str]) -> SectorBlock:
    images = [_image(t, index=i + 1) for i, t in enumerate(flow_titles)]
    return SectorBlock(index=index, title=title, text_content="\n".join(flow_titles), images=images)


def test_flow_match_hints_fund_transfer():
    hints = flow_match_hints("集团资金调拨单（借款）")
    assert "集团资金调拨单" in hints
    assert "集团资金调拨单（已核准）" in hints


def test_ai_app_display_name():
    assert ai_app_display_name("集团费用报销单（已核准）") == "AI_集团费用报销单"


def test_one_row_per_flow_no_dedup(matcher_settings: Settings):
    sectors = [_sector(2, "二、野马集团（二线）", ["集团资金调拨单（已核准）", "集团费用报销单（已核准）"])]
    flows = [
        SectorWorkflowResult(
            sector=sectors[0],
            flows=[_flow("集团资金调拨单（已核准）"), _flow("集团费用报销单（已核准）", index=2)],
        )
    ]
    parse_result = GuideParseResult(source_file="guide.docx", sectors=sectors, sector_results=flows)

    rows = [
        TemplateTableRow(
            row_index=3,
            sheet_name="s1",
            sector_label="野马集团",
            seq="1",
            original_form_name="集团公司内部资金调拨单（借款）",
            backend_flow="集团资金调拨单（借款）",
        ),
        TemplateTableRow(
            row_index=4,
            sheet_name="s1",
            sector_label="野马集团",
            seq="2",
            original_form_name="集团公司内部资金调拨单（还款）",
            backend_flow="集团资金调拨单（还款）",
        ),
    ]

    def fake_form(name, settings=None):
        class Rec:
            success = True
            template_name = name

        return Rec()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.template_matcher.load_form", fake_form)
        report = build_template_match_report(
            settings=matcher_settings,
            parse_result=parse_result,
            template_rows=rows,
        )

    assert len(report.targets) == 2
    assert len(report.rows) == 2
    keys = {t.target_key for t in report.targets}
    assert len(keys) == 2


def test_new_form_when_b_is_xinzeng(matcher_settings: Settings):
    parse_result = GuideParseResult(source_file="guide.docx", sectors=[], sector_results=[])
    rows = [
        TemplateTableRow(
            row_index=3,
            sheet_name="s1",
            sector_label="野马集团",
            seq="1",
            original_form_name="新增",
            backend_flow="集团印鉴使用申请单",
        ),
    ]

    report = build_template_match_report(
        settings=matcher_settings,
        parse_result=parse_result,
        template_rows=rows,
    )

    assert len(report.targets) == 1
    target = report.targets[0]
    assert target.is_new_form is True
    assert target.form_slug == "新增"
    assert target.enabled is True
    assert report.new_form_count == 1


def test_oa_match_multiple_forms_in_b_cell(matcher_settings: Settings):
    parse_result = GuideParseResult(source_file="guide.docx", sectors=[], sector_results=[])

    def fake_form(name, settings=None):
        class Rec:
            success = True
            template_name = name

        return Rec()

    rows = [
        TemplateTableRow(
            row_index=3,
            sheet_name="s1",
            sector_label="金融",
            seq="1",
            original_form_name="表单A\n表单B",
            backend_flow="金融板块（领）借款单",
        ),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.template_matcher.load_form", fake_form)
        slugs, is_new, _, _ = resolve_row_oa_forms("表单A\n表单B", "金融板块（领）借款单", settings=matcher_settings)
        report = build_template_match_report(
            settings=matcher_settings,
            parse_result=parse_result,
            template_rows=rows,
        )

    assert is_new is False
    assert slugs == ["表单A", "表单B"]
    assert report.targets[0].form_slug == "表单A + 表单B"


def test_resolve_row_oa_forms_unmatched_is_new(matcher_settings: Settings):
    slugs, is_new, _, reason = resolve_row_oa_forms("不存在的表单", "某流程", settings=matcher_settings)
    assert slugs == []
    assert is_new is True
    assert "新增" in reason


def test_make_target_key():
    assert make_target_key(2, "集团资金调拨单（已核准）") == "s2:集团资金调拨单"


def test_make_row_target_key():
    row = TemplateTableRow(
        row_index=5,
        sheet_name="sheet1",
        sector_label="x",
        seq="1",
        original_form_name="f",
        backend_flow="集团费用报销单",
    )
    assert make_row_target_key(row) == "sheet1:5:集团费用报销单"
