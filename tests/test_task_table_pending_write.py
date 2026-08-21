"""流程任务表待写入清单读取测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.settings import get_settings
from core.form.task_table import (
    COMPLETED_WRITE_STATUSES,
    load_pending_write_rows,
    load_pending_write_template_rows,
    pending_write_to_template_rows,
)
from schemas.form import TaskTableRow


@pytest.fixture
def task_table_path() -> Path:
    path = get_settings().data_dir / "野马集团流程任务表0816.xlsx"
    if not path.is_file():
        pytest.skip(f"缺少测试数据: {path}")
    return path


def test_pending_write_excludes_completed_status(task_table_path: Path):
    rows = load_pending_write_rows(task_table_path)
    assert rows, "待写入清单不应为空"
    for row in rows:
        assert row.backend_flow.strip()
        assert row.completion_status.strip() not in COMPLETED_WRITE_STATUSES


def test_pending_write_starts_from_row_three(task_table_path: Path):
    rows = load_pending_write_rows(task_table_path)
    assert all(r.row_index >= 2 for r in rows)


def test_pending_write_to_template_rows(task_table_path: Path):
    task_rows = load_pending_write_rows(task_table_path)
    template_rows = pending_write_to_template_rows(task_rows)
    assert len(template_rows) == len(task_rows)
    assert template_rows[0].backend_flow == task_rows[0].backend_flow


def test_load_pending_write_template_rows(task_table_path: Path):
    rows = load_pending_write_template_rows(task_table_path)
    assert len(rows) >= 50


def test_manual_filter_matches_loader():
    row_done = TaskTableRow(
        row_index=3,
        sheet_name="s",
        form_name="f",
        backend_flow="集团费用报销单",
        completion_status="已完成",
    )
    row_pending = TaskTableRow(
        row_index=4,
        sheet_name="s",
        form_name="f",
        backend_flow="集团费用支付单",
        completion_status="",
    )
    filtered = [
        r
        for r in [row_done, row_pending]
        if r.backend_flow.strip() and r.completion_status.strip() not in COMPLETED_WRITE_STATUSES
    ]
    assert filtered == [row_pending]
