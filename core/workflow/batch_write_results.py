"""批量写入结果表：持久化、初始化与合并。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config.settings import Settings, get_settings
from core.form.name_utils import display_form_name
from core.workflow.batch_writer import BatchWriteReport
from core.workflow.template_matcher import TemplateGenerationTarget

# 首次批量写入后已知的 VLM 匹配失败流程（任务表 C 列 / guide_title）
KNOWN_VLM_MATCH_FAILURES: frozenset[str] = frozenset(
    {
        "外贸集团：布尔津矿业费用报销单",
        "外贸集团：布尔津矿业费用支付单",
        "文旅集团财务档案借阅申请表",
        "外贸板块费用报销单",
        "文旅集团文化公司费用借款单",
        "文旅集团文化公司费用报销单",
        "文旅集团文化公司费用支付单",
    }
)

DEFAULT_VLM_MISS_ERROR = (
    "未找到该流程的办事指南流程图 VLM 识别结果。"
    "请先在「流程解析」页完成解析并保存，或检查流程标题映射。"
)


@dataclass
class WriteResultRow:
    target_key: str
    guide_title: str
    app_display_name: str
    oa_forms: str = ""
    write_result: str = "未写入"  # 成功 | 失败 | 未写入
    error_message: str = ""
    form_flow_error: str = "否"  # 是 | 否
    app_id: str = ""
    proc_id: str = ""
    tree_confirmed: bool = False
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WriteResultRow:
        return cls(
            target_key=str(data.get("target_key") or ""),
            guide_title=str(data.get("guide_title") or ""),
            app_display_name=str(data.get("app_display_name") or ""),
            oa_forms=str(data.get("oa_forms") or ""),
            write_result=str(data.get("write_result") or "未写入"),
            error_message=str(data.get("error_message") or ""),
            form_flow_error=str(data.get("form_flow_error") or "否"),
            app_id=str(data.get("app_id") or ""),
            proc_id=str(data.get("proc_id") or ""),
            tree_confirmed=bool(data.get("tree_confirmed")),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass
class WriteResultsTable:
    saved_at: str = ""
    rows: list[WriteResultRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "saved_at": self.saved_at,
            "rows": [r.to_dict() for r in self.rows],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WriteResultsTable:
        rows = [WriteResultRow.from_dict(item) for item in (data.get("rows") or [])]
        return cls(saved_at=str(data.get("saved_at") or ""), rows=rows)


def get_write_results_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.output_dir / "workflows" / "batch_write_results.json"


def _is_known_failure_title(guide_title: str) -> bool:
    norm = display_form_name(guide_title)
    for name in KNOWN_VLM_MATCH_FAILURES:
        if display_form_name(name) == norm or name.strip() == guide_title.strip():
            return True
    return False


def build_initial_write_results(
    targets: list[TemplateGenerationTarget],
    *,
    failed_titles: frozenset[str] | None = None,
) -> WriteResultsTable:
    """初始化完整写入结果表（已知失败标为失败，其余标为成功）。"""
    failed = failed_titles or KNOWN_VLM_MATCH_FAILURES
    now = datetime.now().isoformat(timespec="seconds")
    rows: list[WriteResultRow] = []
    for target in targets:
        is_fail = _title_in_set(target.guide_title, failed)
        rows.append(
            WriteResultRow(
                target_key=target.target_key,
                guide_title=target.guide_title,
                app_display_name=target.app_display_name,
                oa_forms=target.form_slug,
                write_result="失败" if is_fail else "成功",
                error_message=DEFAULT_VLM_MISS_ERROR if is_fail else "",
                form_flow_error="否",
                updated_at=now,
            )
        )
    return WriteResultsTable(saved_at=now, rows=rows)


def _title_in_set(guide_title: str, names: frozenset[str]) -> bool:
    norm = display_form_name(guide_title)
    for name in names:
        if display_form_name(name) == norm or name.strip() == guide_title.strip():
            return True
    return False


def sync_write_results_with_targets(
    table: WriteResultsTable,
    targets: list[TemplateGenerationTarget],
) -> WriteResultsTable:
    """补全新增目标行，保留已有写入结果与人工标记。"""
    by_key = {r.target_key: r for r in table.rows}
    by_title = {display_form_name(r.guide_title): r for r in table.rows}
    now = datetime.now().isoformat(timespec="seconds")
    rows: list[WriteResultRow] = []
    for target in targets:
        existing = by_key.get(target.target_key) or by_title.get(display_form_name(target.guide_title))
        if existing:
            existing.target_key = target.target_key
            existing.guide_title = target.guide_title
            existing.app_display_name = target.app_display_name
            existing.oa_forms = target.form_slug
            if existing.write_result == "未写入" and _title_in_set(
                target.guide_title, KNOWN_VLM_MATCH_FAILURES
            ):
                existing.write_result = "失败"
                existing.error_message = DEFAULT_VLM_MISS_ERROR
            rows.append(existing)
        else:
            is_fail = _title_in_set(target.guide_title, KNOWN_VLM_MATCH_FAILURES)
            rows.append(
                WriteResultRow(
                    target_key=target.target_key,
                    guide_title=target.guide_title,
                    app_display_name=target.app_display_name,
                    oa_forms=target.form_slug,
                    write_result="失败" if is_fail else "未写入",
                    error_message=DEFAULT_VLM_MISS_ERROR if is_fail else "",
                    form_flow_error="否",
                    updated_at=now,
                )
            )
    return WriteResultsTable(saved_at=table.saved_at or now, rows=rows)


def load_write_results(settings: Settings | None = None) -> WriteResultsTable | None:
    path = get_write_results_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return WriteResultsTable.from_dict(data)
    except Exception:
        return None


def save_write_results(table: WriteResultsTable, *, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = get_write_results_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.saved_at = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(table.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_write_results_table(
    targets: list[TemplateGenerationTarget],
    *,
    settings: Settings | None = None,
    force_reinit: bool = False,
) -> WriteResultsTable:
    settings = settings or get_settings()
    if force_reinit:
        table = build_initial_write_results(targets)
        save_write_results(table, settings=settings)
        return table
    loaded = load_write_results(settings)
    if loaded is None:
        table = build_initial_write_results(targets)
        save_write_results(table, settings=settings)
        return table
    synced = sync_write_results_with_targets(loaded, targets)
    save_write_results(synced, settings=settings)
    return synced


def apply_batch_report_to_table(
    table: WriteResultsTable,
    report: BatchWriteReport,
) -> WriteResultsTable:
    """将批量写入报告合并进结果表（按 target_key / guide_title 更新）。"""
    now = datetime.now().isoformat(timespec="seconds")
    item_by_key: dict[str, Any] = {}
    item_by_title: dict[str, Any] = {}
    for item in report.items:
        detail = item.detail or {}
        key = str(detail.get("target_key") or "")
        title = display_form_name(str(detail.get("guide_title") or ""))
        if key:
            item_by_key[key] = item
        if title:
            item_by_title[title] = item

    for row in table.rows:
        item = item_by_key.get(row.target_key) or item_by_title.get(display_form_name(row.guide_title))
        if item is None:
            continue
        row.write_result = "成功" if item.success else "失败"
        row.error_message = "" if item.success else (item.error or DEFAULT_VLM_MISS_ERROR)
        row.app_id = item.app_id
        row.proc_id = item.proc_id
        row.tree_confirmed = item.tree_confirmed
        row.updated_at = now

    return WriteResultsTable(saved_at=now, rows=table.rows)


def _write_result_sort_rank(write_result: str) -> int:
    if write_result == "失败":
        return 0
    if write_result == "未写入":
        return 1
    if write_result == "成功":
        return 2
    return 9


def sorted_write_result_rows(rows: list[WriteResultRow]) -> list[WriteResultRow]:
    """失败流程置顶，其余按流程名排序。"""
    return sorted(rows, key=lambda r: (_write_result_sort_rank(r.write_result), r.guide_title))


def export_write_results_to_excel(
    table: WriteResultsTable,
    *,
    settings: Settings | None = None,
) -> Path:
    """导出写入结果表为 Excel。"""
    import pandas as pd

    settings = settings or get_settings()
    path = settings.output_dir / "workflows" / "batch_write_results.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = table_to_dataframe_rows(table)
    df = pd.DataFrame(rows)
    export_cols = ["流程", "应用名", "OA表单", "表单&流程错误", "写入结果", "失败原因"]
    for col in export_cols:
        if col not in df.columns:
            df[col] = ""
    df = df[export_cols]
    df.to_excel(path, index=False, engine="openpyxl")
    return path


def write_results_excel_bytes(table: WriteResultsTable) -> bytes:
    """生成 Excel 二进制供 Streamlit 下载。"""
    import io

    import pandas as pd

    rows = table_to_dataframe_rows(table)
    df = pd.DataFrame(rows)
    export_cols = ["流程", "应用名", "OA表单", "表单&流程错误", "写入结果", "失败原因"]
    for col in export_cols:
        if col not in df.columns:
            df[col] = ""
    df = df[export_cols]
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


def table_to_dataframe_rows(table: WriteResultsTable) -> list[dict[str, Any]]:
    return [
        {
            "target_key": r.target_key,
            "流程": r.guide_title,
            "应用名": r.app_display_name,
            "OA表单": r.oa_forms,
            "表单&流程错误": r.form_flow_error,
            "写入结果": r.write_result,
            "失败原因": r.error_message,
        }
        for r in sorted_write_result_rows(table.rows)
    ]


def dataframe_rows_to_table(rows: list[dict[str, Any]] | Any, prev: WriteResultsTable) -> WriteResultsTable:
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    edited = {str(item.get("target_key") or ""): item for item in rows}
    merged: list[WriteResultRow] = []
    for r in prev.rows:
        item = edited.get(r.target_key)
        if item is not None:
            r.form_flow_error = str(item.get("表单&流程错误") or "否")
        merged.append(r)
    return WriteResultsTable(saved_at=prev.saved_at, rows=merged)


def select_targets_for_rewrite(
    table: WriteResultsTable,
    targets: list[TemplateGenerationTarget],
    *,
    mode: str,
) -> list[TemplateGenerationTarget]:
    """mode: failed | form_flow_error"""
    by_key = {t.target_key: t for t in targets}
    selected: list[TemplateGenerationTarget] = []
    for row in table.rows:
        if mode == "failed" and row.write_result != "失败":
            continue
        if mode == "form_flow_error" and row.form_flow_error != "是":
            continue
        target = by_key.get(row.target_key)
        if target:
            selected.append(target)
    return selected
