"""读取「可做模板表」Excel（两 sheet，B/C 列映射）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HEADER_MARKERS = ("原系统表单名称", "后端拉取流程")
SHEET_SUFFIX_RE = re.compile(r"[（(][^）)]*[）)]")


@dataclass
class TemplateTableRow:
    row_index: int
    sheet_name: str
    sector_label: str
    seq: str
    original_form_name: str
    backend_flow: str
    remark: str = ""


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _normalize_sector(name: str) -> str:
    text = SHEET_SUFFIX_RE.sub("", (name or "").strip())
    return text or (name or "").strip()


def _is_header_row(a: str, b: str, c: str) -> bool:
    joined = " ".join([a, b, c])
    return any(m in joined for m in HEADER_MARKERS)


def _is_sector_row(a: str, b: str, c: str) -> bool:
    a, b, c = _clean(a), _clean(b), _clean(c)
    if a and not b and not c and not a.isdigit():
        return True
    if not b and bool(c) and not c.isdigit() and len(c) < 40:
        return True
    return False


def _parse_sheet_df(df: pd.DataFrame, *, sheet_name: str) -> list[TemplateTableRow]:
    rows: list[TemplateTableRow] = []
    current_sector = _normalize_sector(sheet_name)

    for idx in range(len(df)):
        col_a = _clean(df.iloc[idx, 0]) if df.shape[1] > 0 else ""
        form_name = _clean(df.iloc[idx, 1]) if df.shape[1] > 1 else ""
        backend_flow = _clean(df.iloc[idx, 2]) if df.shape[1] > 2 else ""
        remark = _clean(df.iloc[idx, 3]) if df.shape[1] > 3 else ""

        if _is_header_row(col_a, form_name, backend_flow):
            continue

        if _is_sector_row(col_a, form_name, backend_flow):
            label = col_a or backend_flow or form_name
            current_sector = _normalize_sector(label) or current_sector
            continue

        if not backend_flow:
            continue

        seq = col_a if col_a.isdigit() else ""
        rows.append(
            TemplateTableRow(
                row_index=idx,
                sheet_name=sheet_name,
                sector_label=current_sector,
                seq=seq,
                original_form_name=form_name,
                backend_flow=backend_flow,
                remark=remark,
            )
        )
    return rows


def load_template_table(path: Path) -> list[TemplateTableRow]:
    """读取工作簿全部 Sheet 的模板表行（以 C 列「后端拉取流程」为准）。"""
    if not path.is_file():
        raise FileNotFoundError(f"模板表不存在: {path}")

    rows: list[TemplateTableRow] = []
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            df = pd.read_excel(workbook, sheet_name=sheet_name, header=None)
            if df.empty:
                continue
            rows.extend(_parse_sheet_df(df, sheet_name=sheet_name))
    return rows


def list_backend_flows(rows: list[TemplateTableRow]) -> list[str]:
    """去重保留顺序的后端拉取流程名列表。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        name = row.backend_flow.strip()
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered
