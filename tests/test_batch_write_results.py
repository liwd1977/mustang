"""批量写入结果表初始化测试。"""

from __future__ import annotations

from core.workflow.batch_write_results import (
    KNOWN_VLM_MATCH_FAILURES,
    build_initial_write_results,
)
from core.workflow.template_matcher import TemplateGenerationTarget


def test_initial_table_marks_known_failures():
    targets = [
        TemplateGenerationTarget(
            target_key=f"k{i}",
            guide_sector_index=2,
            guide_sector_title="x",
            guide_title=title,
            workflow_key=title,
            form_slug="f",
            app_display_name=f"AI_{title}",
        )
        for i, title in enumerate(
            [
                "外贸集团：布尔津矿业费用报销单",
                "集团费用报销单",
            ]
        )
    ]
    table = build_initial_write_results(targets, failed_titles=KNOWN_VLM_MATCH_FAILURES)
    by_title = {r.guide_title: r for r in table.rows}
    assert by_title["外贸集团：布尔津矿业费用报销单"].write_result == "失败"
    assert by_title["集团费用报销单"].write_result == "成功"
