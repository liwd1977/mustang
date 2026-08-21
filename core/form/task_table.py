"""读取流程任务表 Excel（遍历全部 Sheet，B/C/D/F 列）。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from core.form.template_table import TemplateTableRow
from schemas.form import TaskTableRow

HEADER_MARKERS = ("原系统表单名称", "后端拉取流程", "现场拉取流程")
SHEET_SUFFIX_RE = re.compile(r"[（(][^）)]*[）)]")
PENDING_WRITE_START_ROW = 3  # Excel 行号（含表头后首条数据）
COMPLETED_WRITE_STATUSES = frozenset({"已完成", "进行中"})


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _normalize_sector(name: str) -> str:
    text = SHEET_SUFFIX_RE.sub("", (name or "").strip())
    return text or name


def _is_header_row(b: str, c: str, d: str) -> bool:
    return any(m in b for m in HEADER_MARKERS) or any(m in c for m in HEADER_MARKERS)


def _is_sector_row(b: str, c: str, d: str) -> bool:
    return not b and bool(c) and not d


def _parse_sheet_df(
    df: pd.DataFrame,
    *,
    sheet_name: str,
    start_row: int = 1,
    include_completion_status: bool = False,
) -> list[TaskTableRow]:
    rows: list[TaskTableRow] = []
    current_sector = _normalize_sector(sheet_name)
    start_idx = max(start_row - 1, 0)

    for idx in range(start_idx, len(df)):
        seq = _clean(df.iloc[idx, 0])
        form_name = _clean(df.iloc[idx, 1])
        backend_flow = _clean(df.iloc[idx, 2])
        field_flow = _clean(df.iloc[idx, 3]) if df.shape[1] > 3 else ""
        remark = _clean(df.iloc[idx, 4]) if df.shape[1] > 4 else ""
        completion_status = _clean(df.iloc[idx, 5]) if include_completion_status and df.shape[1] > 5 else ""

        if _is_header_row(form_name, backend_flow, field_flow):
            continue
        if seq in {"序号", "NaN"}:
            if _is_sector_row(form_name, backend_flow, field_flow):
                current_sector = _normalize_sector(backend_flow) or current_sector
            continue

        if _is_sector_row(form_name, backend_flow, field_flow):
            current_sector = _normalize_sector(backend_flow) or current_sector
            continue

        if not any([form_name, backend_flow, field_flow]):
            continue

        rows.append(
            TaskTableRow(
                row_index=idx,
                sheet_name=sheet_name,
                seq=seq,
                sector=current_sector,
                form_name=form_name,
                backend_flow=backend_flow,
                field_flow=field_flow,
                remark=remark,
                completion_status=completion_status,
            )
        )
    return rows


def load_task_table(path: Path) -> list[TaskTableRow]:
    """读取工作簿中全部 Sheet 的任务表行。"""
    if not path.is_file():
        raise FileNotFoundError(f"任务表不存在: {path}")

    rows: list[TaskTableRow] = []
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            df = pd.read_excel(workbook, sheet_name=sheet_name, header=None)
            if df.empty:
                continue
            rows.extend(_parse_sheet_df(df, sheet_name=sheet_name))
    return rows


def load_pending_write_rows(path: Path, *, start_row: int = PENDING_WRITE_START_ROW) -> list[TaskTableRow]:
    """读取待写入流程清单：第 3 行起，C 列有值且 F 列非「已完成/进行中」。"""
    if not path.is_file():
        raise FileNotFoundError(f"任务表不存在: {path}")

    rows: list[TaskTableRow] = []
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            df = pd.read_excel(workbook, sheet_name=sheet_name, header=None)
            if df.empty:
                continue
            sheet_rows = _parse_sheet_df(
                df,
                sheet_name=sheet_name,
                start_row=start_row,
                include_completion_status=True,
            )
            for row in sheet_rows:
                if not row.backend_flow.strip():
                    continue
                status = row.completion_status.strip()
                if status in COMPLETED_WRITE_STATUSES:
                    continue
                rows.append(row)
    return rows


def pending_write_to_template_rows(rows: list[TaskTableRow]) -> list[TemplateTableRow]:
    """将流程任务表待写入行转为模板匹配器使用的 TemplateTableRow。"""
    result: list[TemplateTableRow] = []
    for row in rows:
        result.append(
            TemplateTableRow(
                row_index=row.row_index,
                sheet_name=row.sheet_name,
                sector_label=row.sector,
                seq=row.seq,
                original_form_name=row.form_name,
                backend_flow=row.backend_flow,
                remark=row.remark,
            )
        )
    return result


def load_pending_write_template_rows(path: Path) -> list[TemplateTableRow]:
    """读取待写入 C 列流程并转为 TemplateTableRow（单独/批量写入清单）。"""
    return pending_write_to_template_rows(load_pending_write_rows(path))


def list_sheet_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with pd.ExcelFile(path) as workbook:
        return list(workbook.sheet_names)


def summarize_task_table(rows: list[TaskTableRow]) -> dict[str, dict[str, int]]:
    """按 Sheet 统计行数与可提取表单数。"""
    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = stats.setdefault(row.sheet_name or "（未知）", {"rows": 0, "forms": 0})
        bucket["rows"] += 1
        name = (row.form_name or "").strip()
        if name and name != "新增":
            bucket["forms"] += 1
    return stats


def iter_excel_workflow_names(rows: list[TaskTableRow]) -> list[tuple[str, str]]:
    """返回 (流程名, 来源列) 列表，来源为 C 或 D。"""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        for name, source in ((row.backend_flow, "C"), (row.field_flow, "D")):
            key = f"{row.sheet_name}:{source}:{name}"
            if name and key not in seen:
                seen.add(key)
                items.append((name, source))
    return items


def filter_targets_by_sheets(
    rows: list[TaskTableRow],
    sheet_names: list[str] | None,
) -> list[TaskTableRow]:
    targets = iter_oa_form_names(rows)
    if not sheet_names:
        return targets
    allowed = set(sheet_names)
    return [t for t in targets if t.sheet_name in allowed]


def _split_form_cell(name: str) -> list[str]:
    """拆分 B 列单元格中的多个表单名（换行，或「）」后的顿号/逗号）。"""
    parts: list[str] = []
    for line in re.split(r"[\n\r]+", name):
        line = line.strip()
        if not line:
            continue
        # 仅在同单元格多个完整表单名之间切分，避免拆开「（更换、新购）」等括号内顿号
        sub = re.split(r"(?<=[）\)])\s*[、,，]\s*(?=[\u4e00-\u9fff])", line)
        parts.extend(s.strip() for s in sub if s.strip())
    return parts or [name.strip()]


def iter_oa_form_names(rows: list[TaskTableRow]) -> list[TaskTableRow]:
    """返回需要校验/提取 OA 的表单行（排除「新增」和空 B 列）。"""
    result: list[TaskTableRow] = []
    for row in rows:
        name = (row.form_name or "").strip()
        if not name or name == "新增":
            continue
        parts = _split_form_cell(name)
        if len(parts) <= 1:
            result.append(row)
            continue
        for part in parts:
            if part == "新增":
                continue
            result.append(row.model_copy(update={"form_name": part}))
    return result
