"""动态工作流 catalog：从办事指南 + 任务表发现可写入流程。"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.config.settings import Settings, get_settings
from core.form.form_store import load_form
from core.form.name_utils import display_form_name, string_similarity, strip_status_suffix
from core.form.task_table import TaskTableRow, load_task_table
from core.workflow.flow_filter import select_sector_images_within_limit, should_skip_flow_title
from core.workflow.result_store import load_result
from schemas.workflow import GuideParseResult

# 已完整实现的样例流程（不受板块数量限制）
WORKFLOW_CATALOG: dict[str, dict[str, str | list[str]]] = {
    "外贸集团样车合同备案表": {
        "form_slug": "外贸集团样车合同备案表",
        "guide_flow_hints": [
            "外贸集团样车合同备案",
            "外贸集团样车合同备案表",
            "样车合同备案",
        ],
    },
    "野马集团二线招聘需求表": {
        "form_slug": "野马集团二线部门招聘需求表",
        "guide_flow_hints": [
            "野马集团二线招聘需求表（已核对）",
            "野马集团二线招聘需求表",
            "野马集团二线招聘需求",
            "二线招聘需求",
        ],
    },
    "集团费用报销单": {
        "form_slug": "集团公司部门费用报销单",
        "guide_flow_hints": [
            "集团费用报销单（已核准）",
            "集团费用报销单",
            "集团费用报销",
        ],
    },
}

STATIC_SAMPLE_KEYS = frozenset(WORKFLOW_CATALOG.keys())
FORM_MATCH_THRESHOLD = 0.82
TITLE_MATCH_THRESHOLD = 0.92


@dataclass
class WorkflowCatalogEntry:
    workflow_key: str
    guide_title: str
    form_slug: str
    sector_index: int = 0
    guide_flow_hints: list[str] = field(default_factory=list)
    enabled: bool = True
    skip_reason: str = ""
    is_static_sample: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowCatalogEntry:
        return cls(
            workflow_key=str(data.get("workflow_key") or ""),
            guide_title=str(data.get("guide_title") or ""),
            form_slug=str(data.get("form_slug") or ""),
            sector_index=int(data.get("sector_index") or 0),
            guide_flow_hints=list(data.get("guide_flow_hints") or []),
            enabled=bool(data.get("enabled", True)),
            skip_reason=str(data.get("skip_reason") or ""),
            is_static_sample=bool(data.get("is_static_sample")),
        )


def static_sample_key_for_title(title: str) -> str | None:
    """标题是否对应已实现的静态样例流程（精确匹配，避免误伤板块内同名费用流程）。"""
    from core.form.name_utils import display_form_name

    hint = display_form_name(title)
    for key, meta in WORKFLOW_CATALOG.items():
        if hint == display_form_name(key):
            return key
        for guide_hint in meta.get("guide_flow_hints") or []:
            if hint == display_form_name(str(guide_hint)):
                return key
    return None


def get_catalog_hints(workflow_key: str) -> list[str]:
    entry = get_catalog_entry(workflow_key)
    if entry:
        hints = list(entry.guide_flow_hints)
        hints.append(entry.workflow_key)
        hints.append(entry.guide_title)
        return list(dict.fromkeys(h for h in hints if h))
    meta = WORKFLOW_CATALOG.get(workflow_key) or {}
    hints = list(meta.get("guide_flow_hints") or [])
    hints.append(workflow_key)
    return hints


def get_catalog_meta(workflow_key: str, *, settings: Settings | None = None) -> dict[str, str | list[str]]:
    entry = get_catalog_entry(workflow_key, settings=settings)
    if entry is None:
        raise ValueError(f"未知工作流：{workflow_key}")
    if not entry.enabled:
        reason = entry.skip_reason or "未启用"
        raise ValueError(f"工作流「{workflow_key}」不可写入：{reason}")
    return {
        "form_slug": entry.form_slug,
        "guide_flow_hints": list(entry.guide_flow_hints),
    }


def get_catalog_entry(workflow_key: str, *, settings: Settings | None = None) -> WorkflowCatalogEntry | None:
    settings = settings or get_settings()
    for entry in discover_catalog_entries(settings=settings):
        if entry.workflow_key == workflow_key:
            return entry
    return None


def resolve_form_slug(
    guide_title: str,
    task_rows: list[TaskTableRow],
    *,
    settings: Settings | None = None,
) -> tuple[str | None, str]:
    """将办事指南流程标题匹配到任务表 OA 表单名。"""
    settings = settings or get_settings()
    static_key = static_sample_key_for_title(guide_title)
    if static_key:
        return str(WORKFLOW_CATALOG[static_key]["form_slug"]), ""

    best_form = ""
    best_score = 0.0
    for row in task_rows:
        for flow_name in (row.backend_flow, row.field_flow):
            flow_name = (flow_name or "").strip()
            if not flow_name:
                continue
            score = string_similarity(flow_name, guide_title)
            if score > best_score:
                best_score = score
                best_form = row.form_name

    if best_score < FORM_MATCH_THRESHOLD or not best_form:
        return None, f"任务表未匹配 OA 表单（最佳相似度 {best_score:.2f}）"

    record = load_form(best_form, settings)
    if not record or not record.success:
        return None, f"OA 表单「{best_form}」尚未成功提取"
    return best_form, ""


def discover_catalog_entries(
    *,
    settings: Settings | None = None,
    parse_result: GuideParseResult | None = None,
) -> list[WorkflowCatalogEntry]:
    """从 Word 提取结果 + 任务表构建完整 catalog（含静态样例）。"""
    settings = settings or get_settings()
    result = parse_result if parse_result is not None else load_result(settings)
    task_rows: list[TaskTableRow] = []
    if settings.flow_task_table_path.is_file():
        task_rows = load_task_table(settings.flow_task_table_path)

    entries_by_key: dict[str, WorkflowCatalogEntry] = {}

    for key, meta in WORKFLOW_CATALOG.items():
        hints = list(meta.get("guide_flow_hints") or [])
        entries_by_key[key] = WorkflowCatalogEntry(
            workflow_key=key,
            guide_title=key,
            form_slug=str(meta["form_slug"]),
            guide_flow_hints=[key, *hints],
            enabled=True,
            is_static_sample=True,
        )

    if result is None:
        return sorted(entries_by_key.values(), key=lambda e: (e.sector_index, e.workflow_key))

    limit = settings.workflow_per_sector_limit

    for sector in result.sectors:
        if limit > 0:
            allowed_images = {
                (sector.index, img.title_hint, img.index)
                for img in select_sector_images_within_limit(sector, limit)
            }
        else:
            allowed_images = None

        for image in sector.images:
            title = (image.title_hint or "").strip()
            if not title or image.skipped or should_skip_flow_title(title):
                continue

            static_key = static_sample_key_for_title(title)
            if static_key and static_key in entries_by_key:
                entry = entries_by_key[static_key]
                if not entry.sector_index:
                    entry.sector_index = sector.index
                if title not in entry.guide_flow_hints:
                    entry.guide_flow_hints.append(title)
                continue

            if limit > 0 and allowed_images is not None:
                if (sector.index, image.title_hint, image.index) not in allowed_images:
                    continue

            workflow_key = display_form_name(title)
            if workflow_key in entries_by_key:
                continue

            form_slug, reason = resolve_form_slug(title, task_rows, settings=settings)
            hints = list(dict.fromkeys([title, workflow_key, strip_status_suffix(title)]))
            entries_by_key[workflow_key] = WorkflowCatalogEntry(
                workflow_key=workflow_key,
                guide_title=title,
                form_slug=form_slug or "",
                sector_index=sector.index,
                guide_flow_hints=hints,
                enabled=bool(form_slug),
                skip_reason=reason,
            )

    return sorted(entries_by_key.values(), key=lambda e: (e.sector_index, e.is_static_sample, e.workflow_key))


def list_writable_workflow_keys(*, settings: Settings | None = None) -> list[str]:
    return [e.workflow_key for e in discover_catalog_entries(settings=settings) if e.enabled]


def catalog_index_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    out = settings.output_dir / "workflows" / "shenbi"
    out.mkdir(parents=True, exist_ok=True)
    return out / "workflow_catalog.json"


def save_catalog_index(entries: list[WorkflowCatalogEntry], *, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = catalog_index_path(settings)
    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "per_sector_limit": settings.workflow_per_sector_limit,
        "entries": [e.to_dict() for e in entries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_catalog_index(*, settings: Settings | None = None) -> list[WorkflowCatalogEntry]:
    settings = settings or get_settings()
    path = catalog_index_path(settings)
    if not path.is_file():
        return discover_catalog_entries(settings=settings)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [WorkflowCatalogEntry.from_dict(item) for item in data.get("entries") or []]
    except Exception:
        return discover_catalog_entries(settings=settings)
