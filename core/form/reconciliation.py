"""勘误清单：流程名对齐 + OA 表单存在性。"""

from __future__ import annotations

from datetime import datetime

from core.form.semantic_match import (
    collect_unmatched_doc_names,
    collect_unmatched_excel_names,
    match_excel_to_doc_workflows,
)
from core.form.task_table import TaskTableRow, iter_excel_workflow_names, iter_oa_form_names
from core.form.template_search import fetch_oa_catalog, find_template_in_catalog
from core.llm.qwen_client import QwenClient
from core.workflow.result_store import load_result
from schemas.form import MissingOaFormItem, ReconciliationReport, TaskTableRow as TaskRow
from schemas.workflow import GuideParseResult


def collect_doc_workflow_names(result: GuideParseResult | None) -> list[str]:
    if not result:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for sector in result.sectors:
        for image in sector.images:
            if image.skipped:
                continue
            title = (image.title_hint or "").strip()
            if title and title not in seen:
                seen.add(title)
                names.append(title)
    for item in result.sector_results:
        for flow in item.flows:
            title = (flow.title or flow.image.title_hint or "").strip()
            if title and not flow.skipped and title not in seen:
                seen.add(title)
                names.append(title)
    return sorted(names)


def _linked_workflows(row: TaskRow) -> list[str]:
    items = []
    if row.backend_flow:
        items.append(row.backend_flow)
    if row.field_flow:
        items.append(row.field_flow)
    return items


def build_missing_oa_forms(
    rows: list[TaskTableRow],
    catalog: dict[str, list[dict]],
) -> list[MissingOaFormItem]:
    missing: list[MissingOaFormItem] = []
    for row in iter_oa_form_names(rows):
        tpl, score = find_template_in_catalog(row.form_name, catalog)
        if tpl and score >= 0.92:
            continue
        if tpl and score >= 0.72:
            missing.append(
                MissingOaFormItem(
                    task_seq=row.seq,
                    sheet_name=row.sheet_name,
                    form_name=row.form_name,
                    sector=row.sector,
                    oa_status="fuzzy",
                    oa_hint=tpl.get("name", ""),
                    linked_workflows=_linked_workflows(row),
                )
            )
            continue
        missing.append(
            MissingOaFormItem(
                task_seq=row.seq,
                sheet_name=row.sheet_name,
                form_name=row.form_name,
                sector=row.sector,
                oa_status="not_found",
                linked_workflows=_linked_workflows(row),
            )
        )
    return missing


def run_reconciliation(
    task_rows: list[TaskTableRow],
    *,
    parse_result: GuideParseResult | None = None,
    use_llm: bool = True,
    refresh_oa_catalog: bool = False,
) -> ReconciliationReport:
    parse_result = parse_result or load_result()
    doc_names = collect_doc_workflow_names(parse_result)
    excel_items = iter_excel_workflow_names(task_rows)

    client = QwenClient() if use_llm else None
    matches = match_excel_to_doc_workflows(
        excel_items,
        doc_names,
        client=client,
        use_llm=use_llm and bool(client and client.available),
    )

    catalog_result = fetch_oa_catalog(use_cache=not refresh_oa_catalog)
    catalog = catalog_result.catalog if catalog_result.success else {}
    missing_forms = build_missing_oa_forms(task_rows, catalog) if catalog else []

    return ReconciliationReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        doc_workflow_total=len(doc_names),
        excel_workflow_total=len(excel_items),
        workflow_matches=matches,
        unmatched_excel=collect_unmatched_excel_names(matches),
        unmatched_doc=collect_unmatched_doc_names(matches, doc_names),
        missing_oa_forms=[m for m in missing_forms if m.oa_status == "not_found"],
        oa_template_total=sum(len(v) for v in catalog.values()) if catalog else 0,
    )
