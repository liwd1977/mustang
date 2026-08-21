"""任务表与本地表单存储的对账：累计统计、失败清单、重试目标。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.config.settings import Settings, get_settings
from core.form.form_store import load_form
from core.form.task_table import TaskTableRow, iter_oa_form_names, load_task_table

STATUS_NAME = "extraction_status.json"


def status_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.forms_store_dir / STATUS_NAME


def _target_status(row: TaskTableRow, settings: Settings) -> str:
    record = load_form(row.form_name, settings)
    if record is None:
        return "missing"
    if record.success:
        return "success"
    return "failed"


def summarize_extraction(
    targets: list[TaskTableRow] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    """对比任务表 B 列与本地存储，返回累计统计与失败/缺失清单。"""
    settings = settings or get_settings()
    if targets is None:
        rows = load_task_table(settings.flow_task_table_path)
        targets = iter_oa_form_names(rows)

    success_rows: list[dict] = []
    failed_rows: list[dict] = []
    missing_rows: list[dict] = []
    seen_failed: set[str] = set()
    seen_missing: set[str] = set()
    unique_success: set[str] = set()

    for row in targets:
        status = _target_status(row, settings)
        item = {
            "sheet": row.sheet_name,
            "form": row.form_name,
            "seq": row.seq,
        }
        if status == "success":
            success_rows.append(item)
            unique_success.add(row.form_name)
        elif status == "failed":
            record = load_form(row.form_name, settings)
            item["error"] = (record.error if record else "") or "未知错误"
            failed_rows.append(item)
            seen_failed.add(row.form_name)
        else:
            missing_rows.append(item)
            seen_missing.add(row.form_name)

    unique_targets = {t.form_name for t in targets}
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_targets": len(targets),
        "unique_targets": len(unique_targets),
        "stored_success_unique": len(unique_success),
        "stored_failed_unique": len(seen_failed),
        "stored_missing_unique": len(seen_missing),
        "row_success": len(success_rows),
        "row_failed": len(failed_rows),
        "row_missing": len(missing_rows),
        "success_rows": success_rows,
        "failed": failed_rows,
        "missing": missing_rows,
    }


def save_extraction_status(
    targets: list[TaskTableRow] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    summary = summarize_extraction(targets, settings=settings)
    path = status_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_extraction_status(settings: Settings | None = None) -> dict:
    path = status_path(settings)
    if not path.is_file():
        return save_extraction_status(settings=settings)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return save_extraction_status(settings=settings)


def iter_failed_targets(
    targets: list[TaskTableRow] | None = None,
    *,
    settings: Settings | None = None,
) -> list[TaskTableRow]:
    """返回提取失败的表单（本地有失败记录），按表单名去重保留首条。"""
    from core.form.form_store import filter_failed_targets

    settings = settings or get_settings()
    if targets is None:
        rows = load_task_table(settings.flow_task_table_path)
        targets = iter_oa_form_names(rows)
    return filter_failed_targets(targets, settings=settings)


def iter_retry_targets(
    targets: list[TaskTableRow] | None = None,
    *,
    settings: Settings | None = None,
) -> list[TaskTableRow]:
    """兼容旧名：等同 iter_failed_targets。"""
    return iter_failed_targets(targets, settings=settings)
