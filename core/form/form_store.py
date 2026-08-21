"""原始表单本地存储与模糊检索。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from core.config.settings import Settings, get_settings
from schemas.form import RawFormRecord

INDEX_NAME = "index.json"


def _slug(name: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name.strip())
    return text.strip("_") or "form"


def get_forms_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.forms_store_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path(settings: Settings | None = None) -> Path:
    return get_forms_dir(settings) / INDEX_NAME


def _load_index(settings: Settings | None = None) -> list[dict]:
    path = _index_path(settings)
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(items: list[dict], settings: Settings | None = None) -> None:
    _index_path(settings).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def save_form(record: RawFormRecord, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    forms_dir = get_forms_dir(settings)
    if not record.extracted_at:
        record.extracted_at = datetime.now().isoformat(timespec="seconds")

    slug = _slug(record.template_name)
    store_path = forms_dir / f"{slug}.json"
    store_path.write_text(json.dumps(record.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    index = _load_index(settings)
    entry = {
        "template_name": record.template_name,
        "template_id": record.template_id,
        "slug": slug,
        "path": str(store_path),
        "success": record.success,
        "extracted_at": record.extracted_at,
        "field_count": len(record.fields),
        "sheet_name": record.sheet_name,
    }
    index = [x for x in index if x.get("slug") != slug]
    index.append(entry)
    index.sort(key=lambda x: x.get("template_name", ""))
    _save_index(index, settings)
    return store_path


def load_form(template_name: str, settings: Settings | None = None) -> RawFormRecord | None:
    slug = _slug(template_name)
    path = get_forms_dir(settings) / f"{slug}.json"
    if not path.is_file():
        return None
    return RawFormRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))


def list_forms(settings: Settings | None = None) -> list[dict]:
    return _load_index(settings)


def _score(query: str, name: str) -> float:
    q, n = query.lower(), name.lower()
    if q in n:
        return 0.95 + 0.05 * (len(q) / max(len(n), 1))
    return SequenceMatcher(None, q, n).ratio()


def form_already_extracted(template_name: str, *, success_only: bool = True, settings: Settings | None = None) -> bool:
    record = load_form(template_name, settings)
    if record is None:
        return False
    return record.success if success_only else True


def filter_pending_targets(
    targets: list,
    *,
    skip_existing: bool = True,
    settings: Settings | None = None,
) -> tuple[list, int]:
    """断点续跑：跳过已成功项，仍包含失败项（兼容旧逻辑）。"""
    if not skip_existing:
        return targets, 0
    pending = []
    skipped = 0
    for row in targets:
        if form_already_extracted(row.form_name, success_only=True, settings=settings):
            skipped += 1
        else:
            pending.append(row)
    return pending, skipped


def filter_unextracted_targets(
    targets: list,
    *,
    settings: Settings | None = None,
) -> tuple[list, int]:
    """仅返回从未提取过的表单（本地无记录），用于「开始批量提取」。"""
    settings = settings or get_settings()
    pending = []
    skipped = 0
    for row in targets:
        if load_form(row.form_name, settings) is None:
            pending.append(row)
        else:
            skipped += 1
    return pending, skipped


def filter_failed_targets(
    targets: list,
    *,
    settings: Settings | None = None,
) -> list:
    """返回提取失败的表单（本地有记录且 success=False），按表单名去重。"""
    settings = settings or get_settings()
    pending: list = []
    seen: set[str] = set()
    for row in targets:
        if row.form_name in seen:
            continue
        record = load_form(row.form_name, settings)
        if record and not record.success:
            pending.append(row)
            seen.add(row.form_name)
    return pending


def search_forms(query: str, *, limit: int = 20, settings: Settings | None = None) -> list[dict]:
    query = (query or "").strip()
    index = _load_index(settings)
    if not query:
        return index[:limit]
    scored = [(_score(query, item.get("template_name", "")), item) for item in index]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{**item, "match_score": round(score, 3)} for score, item in scored[:limit] if score >= 0.3]
