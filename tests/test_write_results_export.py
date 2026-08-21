"""写入结果 Excel 导出测试。"""

from __future__ import annotations

from core.workflow.batch_write_results import (
    WriteResultRow,
    WriteResultsTable,
    export_write_results_to_excel,
    write_results_excel_bytes,
)


def test_export_write_results_excel(tmp_path):
    table = WriteResultsTable(
        rows=[
            WriteResultRow(
                target_key="k1",
                guide_title="集团费用报销单",
                app_display_name="AI_集团费用报销单",
                oa_forms="集团费用报销单",
                write_result="成功",
            )
        ]
    )
    from core.config.settings import Settings

    settings = Settings(output_dir=tmp_path)
    path = export_write_results_to_excel(table, settings=settings)
    assert path.is_file()
    assert path.suffix == ".xlsx"
    data = write_results_excel_bytes(table)
    assert len(data) > 100
