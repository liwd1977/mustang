"""OA 公共模板目录检索。"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from core.config.settings import Settings, get_settings
from core.oa.template_catalog import TemplateCatalogResult, run_list_public_templates

CATALOG_CACHE = "oa_template_catalog.json"


def flatten_catalog(catalog: dict[str, list[dict]]) -> list[dict]:
    items: list[dict] = []
    for category, templates in catalog.items():
        for tpl in templates:
            items.append(
                {
                    "name": tpl.get("name", ""),
                    "id": tpl.get("id", ""),
                    "type": tpl.get("type", ""),
                    "category": category,
                }
            )
    return items


def get_catalog_cache_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.output_dir / "oa_playwright" / CATALOG_CACHE


def load_cached_catalog(settings: Settings | None = None) -> TemplateCatalogResult | None:
    path = get_catalog_cache_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TemplateCatalogResult(
            success=bool(data.get("success")),
            subdirs=list(data.get("subdirs") or []),
            catalog=data.get("catalog") or {},
            error_message=data.get("error_message", ""),
            raw=data.get("raw") or {},
        )
    except Exception:
        return None


def save_catalog_cache(result: TemplateCatalogResult, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = get_catalog_cache_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "success": result.success,
                "subdirs": result.subdirs,
                "catalog": result.catalog,
                "error_message": result.error_message,
                "raw": result.raw,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def fetch_oa_catalog(settings: Settings | None = None, *, use_cache: bool = True) -> TemplateCatalogResult:
    settings = settings or get_settings()
    if use_cache:
        cached = load_cached_catalog(settings)
        if cached and cached.success:
            return cached

    result = run_list_public_templates(
        settings.oa_base_url,
        settings.oa_username or "1号",
        settings.oa_password,
        template_url=settings.oa_template_url,
    )
    if result.success:
        save_catalog_cache(result, settings)
    return result


def _name_score(query: str, candidate: str) -> float:
    q, c = query.strip(), candidate.strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.92
    return SequenceMatcher(None, q, c).ratio()


def find_template_in_catalog(name: str, catalog: dict[str, list[dict]]) -> tuple[dict | None, float]:
    flat = flatten_catalog(catalog)
    best: dict | None = None
    best_score = 0.0
    for item in flat:
        score = _name_score(name, item.get("name", ""))
        if score > best_score:
            best_score = score
            best = item
    if best_score < 0.72:
        return None, best_score
    return best, best_score


def all_template_names(catalog: dict[str, list[dict]]) -> list[str]:
    return [item.get("name", "") for item in flatten_catalog(catalog) if item.get("name")]
