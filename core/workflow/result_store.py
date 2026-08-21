"""解析结果持久化：保存与加载 GuideParseResult。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.config.settings import Settings, get_settings
from core.workflow.node_graph import finalize_workflow_nodes
from schemas.workflow import GuideParseResult, SectorBlock, WorkflowFlowResult, WorkflowImage


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


def is_parse_run_complete(result: GuideParseResult | None) -> bool:
    """VLM 全量识别是否已完成（非中断的 partial 缓存）。"""
    if result is None:
        return False
    return bool(getattr(result, "parse_finished_at", ""))


def upsert_flow_in_result(
    result: GuideParseResult,
    sector_index: int,
    flow: WorkflowFlowResult,
    *,
    settings: Settings | None = None,
) -> GuideParseResult:
    """将单流程 VLM 结果写入/更新 parse_result 缓存。"""
    from schemas.workflow import SectorWorkflowResult

    settings = settings or get_settings()
    sector_block = next((s for s in result.sectors if s.index == sector_index), None)
    if sector_block is None:
        raise ValueError(f"板块 {sector_index} 不存在于 Word 提取结果")

    item = next((sr for sr in result.sector_results if sr.sector.index == sector_index), None)
    if item is None:
        item = SectorWorkflowResult(sector=sector_block, flows=[])
        result.sector_results.append(item)

    image_key = (flow.image.filename or flow.image.path or str(flow.image.index)).lower()
    replaced = False
    for idx, existing in enumerate(item.flows):
        ex_key = (existing.image.filename or existing.image.path or str(existing.image.index)).lower()
        if ex_key and ex_key == image_key:
            item.flows[idx] = flow
            replaced = True
            break
    if not replaced:
        item.flows.append(flow)

    if flow.success and flow.nodes:
        parsed = sum(1 for sr in result.sector_results for f in sr.flows if f.success and f.nodes)
        result.parsed_flowcharts = parsed
    save_result(result, settings=settings)
    return result


def _missing_parsed_flow_message(
    workflow_name: str,
    *,
    settings: Settings,
    result: GuideParseResult | None,
) -> str:
    lines = [f"未找到「{workflow_name}」的办事指南流程图 VLM 识别结果。"]
    if settings.guide_parse_use_cache:
        lines.append("当前为批量模式（GUIDE_PARSE_USE_CACHE=true），请先在「流程解析」页完成解析并保存。")
    else:
        lines.append("开发模式已尝试从 Word 自动识别该流程，但仍未成功。")
        if not settings.llm_configured:
            lines.append("请配置 QWEN_API_KEY / OPENAI_API_KEY 后重试。")
    if result is not None:
        total = (result.total_flowcharts or 0) - (result.skipped_flowcharts or 0)
        parsed = result.parsed_flowcharts or 0
        if not is_parse_run_complete(result) and parsed < total:
            lines.append(f"磁盘缓存为未完成状态（已识别 {parsed}/{total} 个流程图）。")
        titles = []
        for item in result.sector_results:
            for flow in item.flows:
                if flow.success and flow.nodes:
                    titles.append(flow.title or flow.image.title_hint)
        if titles:
            preview = "、".join(titles[:8])
            if len(titles) > 8:
                preview += f" 等 {len(titles)} 个"
            lines.append(f"缓存中已有：{preview}")
    return " ".join(lines)


def resolve_parsed_flow(workflow_name: str, *, settings: Settings | None = None):
    """获取 catalog 流程的 parse 结果；开发模式缺省时自动 VLM 识别该流程图。"""
    from core.workflow.pipeline import recognize_catalog_workflow
    from core.workflow.shenbi_builder import find_parsed_flow, parsed_flow_matches_catalog

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
        flow = find_parsed_flow(workflow_name, settings=settings)
        if flow and flow.nodes:
            return flow
        raise ValueError(_missing_parsed_flow_message(workflow_name, settings=settings, result=result))

    stale = result is None or is_result_stale(result, settings)
    if not stale:
        flow = find_parsed_flow(workflow_name, settings=settings)
        if flow and flow.nodes and parsed_flow_matches_catalog(workflow_name, flow):
            return flow

    flow = recognize_catalog_workflow(workflow_name, settings=settings)
    if flow and flow.nodes and parsed_flow_matches_catalog(workflow_name, flow):
        return flow

    result = load_result(settings)
    raise ValueError(_missing_parsed_flow_message(workflow_name, settings=settings, result=result))


def ensure_guide_parse_fresh(settings: Settings | None = None) -> GuideParseResult:
    """按配置保证 parse_result 与当前 Word 一致。

    - guide_parse_use_cache=False（开发试验，默认）：缓存缺失、Word 已更新或全量解析未完成时自动 run_guide_pipeline。
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

    if result is not None and not is_result_stale(result, settings) and is_parse_run_complete(result):
        return result

    from core.workflow.pipeline import run_guide_pipeline

    return run_guide_pipeline()


def clear_guide_parse_storage(*, settings: Settings | None = None) -> list[str]:
    """清空办事指南解析缓存（parse_result、切图目录、神笔生成物等）。"""
    import shutil

    settings = settings or get_settings()
    removed: list[str] = []
    workflows_root = settings.output_dir / "workflows"
    if not workflows_root.is_dir():
        return removed

    for path in sorted(workflows_root.iterdir()):
        if path.is_file() and path.name.endswith("_parse_result.json"):
            path.unlink(missing_ok=True)
            removed.append(str(path))
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(str(path))

    return removed


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
