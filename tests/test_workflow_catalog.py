"""workflow_catalog 单元测试。"""

from __future__ import annotations

import pytest

from core.config.settings import Settings, get_settings
from core.workflow.flow_filter import should_skip_flow_title
from core.workflow.workflow_catalog import (
    WORKFLOW_CATALOG,
    discover_catalog_entries,
    resolve_form_slug,
    static_sample_key_for_title,
)
from schemas.form import TaskTableRow
from schemas.workflow import GuideParseResult, SectorBlock, WorkflowImage


@pytest.fixture
def catalog_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("WORKFLOW_PER_SECTOR_LIMIT", "2")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _image(title: str, *, index: int = 1, skipped: bool = False) -> WorkflowImage:
    return WorkflowImage(index=index, title_hint=title, filename=f"flow_{index:02d}.png", skipped=skipped)


def _sector(index: int, titles: list[str]) -> SectorBlock:
    lines: list[str] = []
    images: list[WorkflowImage] = []
    for i, title in enumerate(titles):
        lines.extend([title, "职能部门：测试", "办公地点：", "联系电话："])
        images.append(
            _image(title, index=i + 1, skipped=should_skip_flow_title(title)),
        )
    return SectorBlock(
        index=index,
        title=f"板块{index}",
        text_content="\n".join(lines),
        images=images,
    )


def test_should_skip_paused_and_unused():
    assert should_skip_flow_title("集团费用报销单（暂停）")
    assert should_skip_flow_title("某流程（暂未使用）")
    assert not should_skip_flow_title("集团费用报销单（已核准）")


def test_static_sample_key_for_title():
    assert static_sample_key_for_title("集团费用报销单（已核准）") == "集团费用报销单"
    assert static_sample_key_for_title("外贸集团样车合同备案表") == "外贸集团样车合同备案表"


def test_discover_includes_static_samples(catalog_settings: Settings):
    result = GuideParseResult(
        source_file="guide.docx",
        sectors=[_sector(2, ["其他流程A", "其他流程B"])],
    )
    entries = discover_catalog_entries(settings=catalog_settings, parse_result=result)
    static_keys = {e.workflow_key for e in entries if e.is_static_sample}
    assert static_keys == set(WORKFLOW_CATALOG.keys())


def test_discover_per_sector_limit(catalog_settings: Settings):
    titles = [f"流程{i}" for i in range(1, 6)]
    result = GuideParseResult(source_file="guide.docx", sectors=[_sector(3, titles)])
    rows = [
        TaskTableRow(row_index=1, form_name=f"表单{i}", backend_flow=f"流程{i}") for i in range(1, 6)
    ]

    def fake_load_form(name, settings=None):
        class Rec:
            success = True

        return Rec()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.workflow_catalog.load_form", fake_load_form)
        entries = discover_catalog_entries(settings=catalog_settings, parse_result=result)
        # mock task table via resolve - we need to pass task rows differently
        # Instead patch load_task_table
        mp.setattr("core.workflow.workflow_catalog.load_task_table", lambda path: rows)
        entries = discover_catalog_entries(settings=catalog_settings, parse_result=result)

    dynamic = [e for e in entries if not e.is_static_sample]
    assert len(dynamic) == 2


def test_discover_respects_document_order_not_image_index(catalog_settings: Settings):
    """板块内应按办事指南正文顺序取前 N 个，而非 images 数组顺序。"""
    titles = [
        "文旅集团费用借款单",
        "文旅集团费用支付单",
        "文旅集团采购借款单",
    ]
    sector = _sector(4, titles)
    # Word 提取顺序与正文不一致：第 2 张图误为采购借款
    sector.images[1].title_hint = "文旅集团采购借款单"
    sector.images[2].title_hint = "文旅集团费用支付单"
    result = GuideParseResult(source_file="guide.docx", sectors=[sector])
    rows = [
        TaskTableRow(row_index=1, form_name="表单A", backend_flow="文旅集团费用借款单"),
        TaskTableRow(row_index=2, form_name="表单B", backend_flow="文旅集团费用支付单"),
        TaskTableRow(row_index=3, form_name="表单C", backend_flow="文旅集团采购借款单"),
    ]

    def fake_load_form(name, settings=None):
        class Rec:
            success = True

        return Rec()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.workflow_catalog.load_task_table", lambda path: rows)
        mp.setattr("core.workflow.workflow_catalog.load_form", fake_load_form)
        entries = discover_catalog_entries(settings=catalog_settings, parse_result=result)

    keys = {e.workflow_key for e in entries if not e.is_static_sample}
    assert "文旅集团费用借款单" in keys
    assert "文旅集团费用支付单" in keys
    assert "文旅集团采购借款单" not in keys


def test_foreign_trade_sector_picks_first_two_by_document_order(catalog_settings: Settings):
    titles = [
        "外贸板块费用报销单（已核准）",
        "外贸集团费用支付单（已核准）",
        "外贸集团费用借款单（已核准）",
        "野马集团客商档案表（已核准）",
    ]
    sector = _sector(3, titles)
    sector.images[2].title_hint = "野马集团客商档案表（已核准）"
    sector.images[3].title_hint = "外贸集团费用支付单（已核准）"
    result = GuideParseResult(source_file="guide.docx", sectors=[sector])
    rows = [
        TaskTableRow(row_index=1, form_name="表单A", backend_flow="外贸板块费用报销单（已核准）"),
        TaskTableRow(row_index=2, form_name="表单B", backend_flow="外贸集团费用支付单（已核准）"),
        TaskTableRow(row_index=3, form_name="表单C", backend_flow="野马集团客商档案表（已核准）"),
    ]

    def fake_load_form(name, settings=None):
        class Rec:
            success = True

        return Rec()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.workflow_catalog.load_task_table", lambda path: rows)
        mp.setattr("core.workflow.workflow_catalog.load_form", fake_load_form)
        entries = discover_catalog_entries(settings=catalog_settings, parse_result=result)

    keys = {e.workflow_key for e in entries if not e.is_static_sample}
    assert "外贸板块费用报销单" in keys
    assert "外贸集团费用支付单" in keys
    assert "野马集团客商档案表" not in keys


def test_discover_skips_paused_in_word(catalog_settings: Settings):
    result = GuideParseResult(
        source_file="guide.docx",
        sectors=[_sector(4, ["正常流程", "废弃流程（暂停）", "第三流程"])],
    )
    rows = [
        TaskTableRow(row_index=1, form_name="表单A", backend_flow="正常流程"),
        TaskTableRow(row_index=2, form_name="表单C", backend_flow="第三流程"),
    ]

    def fake_load_form(name, settings=None):
        class Rec:
            success = True

        return Rec()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.workflow_catalog.load_task_table", lambda path: rows)
        mp.setattr("core.workflow.workflow_catalog.load_form", fake_load_form)
        entries = discover_catalog_entries(settings=catalog_settings, parse_result=result)

    keys = {e.workflow_key for e in entries if not e.is_static_sample}
    assert "正常流程" in keys
    assert "第三流程" in keys
    assert not any("暂停" in k for k in keys)


def test_resolve_form_slug_from_task_table(catalog_settings: Settings):
    rows = [
        TaskTableRow(row_index=1, form_name="集团公司部门费用报销单", backend_flow="集团费用报销单（已核准）"),
    ]

    def fake_load_form(name, settings=None):
        assert name == "集团公司部门费用报销单"
        class Rec:
            success = True
        return Rec()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.workflow.workflow_catalog.load_form", fake_load_form)
        slug, reason = resolve_form_slug("集团费用报销单（已核准）", rows, settings=catalog_settings)

    assert slug == "集团公司部门费用报销单"
    assert reason == ""
