"""从 OA 批量提取原始表单并写入本地存储。"""

from __future__ import annotations

import json
from datetime import datetime

from core.config.settings import Settings, get_settings
from core.form.form_store import save_form
from core.form.task_table import TaskTableRow, iter_oa_form_names
from core.oa.form_parser import run_parse_raw
from schemas.form import RawFormField, RawFormRecord
from schemas.form_batch import BatchExtractResult


def _to_record(
    result,
    *,
    task_seq: str = "",
    sheet_name: str = "",
    linked_workflows: list[str] | None = None,
    oa_category: str = "",
) -> RawFormRecord:
    fields = [
        RawFormField(
            label=f.label,
            input_type=f.input_type,
            tag=f.tag,
            field_id=f.field_id,
        )
        for f in result.fields
    ]
    return RawFormRecord(
        template_name=result.template_name,
        template_id=result.template_id,
        template_url=result.template_url,
        oa_category=oa_category,
        success=result.success,
        error=result.error_message,
        start_marker=result.start_marker,
        end_marker=result.end_marker,
        fields=fields,
        raw=result.raw,
        extracted_at=datetime.now().isoformat(timespec="seconds"),
        task_table_seq=task_seq,
        sheet_name=sheet_name,
        linked_workflows=linked_workflows or [],
    )


def extract_raw_form(
    template_name: str,
    *,
    settings: Settings | None = None,
    task_seq: str = "",
    sheet_name: str = "",
    linked_workflows: list[str] | None = None,
) -> RawFormRecord:
    settings = settings or get_settings()
    result = run_parse_raw(
        settings.oa_base_url,
        settings.oa_username or "1号",
        settings.oa_password,
        template_url=settings.oa_template_url,
        template_name=template_name,
    )
    record = _to_record(
        result,
        task_seq=task_seq,
        sheet_name=sheet_name,
        linked_workflows=linked_workflows,
        oa_category=str(result.raw.get("category", "")),
    )
    save_form(record, settings)
    return record


def extract_forms_batch(
    rows: list[TaskTableRow],
    *,
    sheet_names: list[str] | None = None,
    skip_existing: bool = True,
    settings: Settings | None = None,
    on_progress=None,
) -> BatchExtractResult:
    """按任务表批量提取（同步，含防休眠与重试）。rows 仅作 API 兼容，实际从任务表文件读取。"""
    from core.form.batch_runner import run_batch_job

    _ = rows
    _ = on_progress
    return run_batch_job(
        sheet_names=sheet_names,
        skip_existing=skip_existing,
        settings=settings,
    )


def extract_forms_from_task_table(
    rows: list[TaskTableRow],
    *,
    settings: Settings | None = None,
    on_progress=None,
) -> list[RawFormRecord]:
    """兼容旧接口：全量批量提取。"""
    batch = extract_forms_batch(rows, skip_existing=False, settings=settings, on_progress=on_progress)
    return batch.records
