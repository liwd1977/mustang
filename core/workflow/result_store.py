"""解析结果持久化：保存与加载 GuideParseResult。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.config.settings import Settings, get_settings
from core.workflow.node_graph import finalize_workflow_nodes
from schemas.workflow import GuideParseResult


def get_store_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.output_dir / "workflows" / f"{settings.guide_output_stem}_parse_result.json"


def doc_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def save_result(
    result: GuideParseResult,
    *,
    section_from: int = 2,
    section_to: int = 10,
    vlm_flow_limit: int = 0,
    settings: Settings | None = None,
) -> Path:
    """写入磁盘并返回存储路径。"""
    settings = settings or get_settings()
    store_path = get_store_path(settings)
    store_path.parent.mkdir(parents=True, exist_ok=True)

    doc_path = Path(result.source_file) if result.source_file else settings.guide_docx_path
    result.saved_at = datetime.now().isoformat(timespec="seconds")
    result.source_mtime = doc_mtime(doc_path)
    result.section_from = section_from
    result.section_to = section_to
    result.vlm_flow_limit = vlm_flow_limit

    store_path.write_text(
        json.dumps(result.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return store_path


def _enrich_flow_nodes(result: GuideParseResult) -> GuideParseResult:
    for item in result.sector_results:
        for flow in item.flows:
            if flow.nodes:
                finalize_workflow_nodes(flow.nodes)
    return result


def normalize_parse_result(result: GuideParseResult | None) -> GuideParseResult | None:
    """兼容旧版缓存/session 对象，补全耗时等新增字段。"""
    if result is None:
        return None
    data = result.model_dump()
    data.setdefault("parse_started_at", "")
    data.setdefault("parse_finished_at", "")
    return GuideParseResult.model_validate(data)


def load_result(settings: Settings | None = None) -> GuideParseResult | None:
    """从磁盘加载解析结果，不存在或损坏则返回 None。"""
    settings = settings or get_settings()
    store_path = get_store_path(settings)
    if not store_path.is_file():
        return None
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
        data.setdefault("parse_started_at", "")
        data.setdefault("parse_finished_at", "")
        result = GuideParseResult.model_validate(data)
        return _enrich_flow_nodes(result)
    except Exception:
        return None


def is_result_stale(result: GuideParseResult, settings: Settings | None = None) -> bool:
    """源 Word 文档已变更则视为过期。"""
    settings = settings or get_settings()
    doc_path = settings.guide_docx_path
    if not doc_path.is_file():
        return True
    stored = result.source_mtime
    current = doc_mtime(doc_path)
    if stored <= 0 or current <= 0:
        return False
    return abs(current - stored) > 1e-6


def ensure_guide_parse_fresh(settings: Settings | None = None) -> GuideParseResult:
    """按配置保证 parse_result 与当前 Word 一致。

    - guide_parse_use_cache=False（开发试验，默认）：缓存缺失或 Word 已更新时自动 run_guide_pipeline。
    - guide_parse_use_cache=True（批量）：仅用磁盘缓存；过期则提示用户在流程解析页手动重新解析。
    """
    settings = settings or get_settings()
    result = load_result(settings)

    if settings.guide_parse_use_cache:
        if result is None:
            raise ValueError("未找到流程解析缓存，请先在「流程解析」页完成解析并保存")
        if is_result_stale(result, settings):
            raise ValueError(
                f"办事指南 Word 已更新，解析缓存已过期（缓存于 {result.saved_at or '未知'}）。"
                "请在「流程解析」页点击「重新解析」后再生成/写入。"
            )
        return result

    if result is not None and not is_result_stale(result, settings):
        return result

    from core.workflow.pipeline import run_guide_pipeline

    return run_guide_pipeline()


def get_store_meta(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    store_path = get_store_path(settings)
    if not store_path.is_file():
        return {"exists": False, "path": str(store_path)}
    result = load_result(settings)
    if result is None:
        return {"exists": True, "path": str(store_path), "valid": False}
    return {
        "exists": True,
        "valid": True,
        "path": str(store_path),
        "saved_at": result.saved_at,
        "source_file": result.source_file,
        "total_flowcharts": result.total_flowcharts,
        "parsed_flowcharts": result.parsed_flowcharts,
        "sector_count": len(result.sectors),
        "stale": is_result_stale(result, settings),
    }
