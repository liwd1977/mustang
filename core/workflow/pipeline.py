"""办事指南解析管线：Word 提取 + VLM 识别。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from core.config.settings import get_settings, reload_settings
from core.form.name_utils import normalize_workflow_name
from core.llm.qwen_client import QwenClient
from core.logging.delivery_logger import DeliveryRunLogger, setup_delivery_logger
from core.workflow.flow_filter import dedupe_sector_flows, format_flow_display_title
from core.workflow.result_store import load_result, save_result
from core.workflow.vlm_recognizer import (
    placeholder_flow,
    recognize_workflow_flow,
    skipped_flow,
)
from core.workflow.word_parser import parse_guide_docx, parse_guide_docx_from_bytes
from schemas.workflow import GuideParseResult, SectorWorkflowResult, WorkflowFlowResult, WorkflowImage

ProgressCallback = Callable[[int, int, str], None]

SHIELDED_FLOW_MSG = "暂仅解析首个流程（调试模式，其余 VLM 已屏蔽）"


def _count_flowcharts(sectors) -> tuple[int, int, int]:
    total = sum(len(s.images) for s in sectors)
    skipped = sum(1 for s in sectors for img in s.images if img.skipped)
    to_parse = total - skipped
    return total, skipped, to_parse


def _effective_parse_limit(to_parse: int, flow_limit: int) -> int:
    if flow_limit <= 0:
        return to_parse
    return min(to_parse, flow_limit)


def _flow_cache_keys(sector_index: int, image: WorkflowImage) -> list[tuple[int, str, str]]:
    title_key = normalize_workflow_name(image.title_hint)
    path_key = (image.path or "").replace("\\", "/")
    file_key = image.filename or ""
    keys: list[tuple[int, str, str]] = []
    if title_key:
        keys.append((sector_index, title_key, file_key or path_key or title_key))
    if path_key:
        keys.append((sector_index, title_key or path_key, path_key))
    if file_key:
        keys.append((sector_index, title_key or file_key, file_key))
    return keys


def _load_cached_flow_map(settings) -> dict[tuple[int, str, str], WorkflowFlowResult]:
    previous = load_result(settings)
    cached: dict[tuple[int, str, str], WorkflowFlowResult] = {}
    if not previous or not previous.sector_results:
        return cached
    for item in previous.sector_results:
        for flow in item.flows:
            if not flow.success or not flow.nodes:
                continue
            for key in _flow_cache_keys(item.sector.index, flow.image):
                cached[key] = flow
    return cached


def _reuse_cached_flow(
    *,
    sector_index: int,
    image: WorkflowImage,
    cached: dict[tuple[int, str, str], WorkflowFlowResult],
) -> WorkflowFlowResult | None:
    for key in _flow_cache_keys(sector_index, image):
        flow = cached.get(key)
        if not flow:
            continue
        old_path = flow.image.path
        if old_path and not Path(old_path).is_file() and image.path and Path(image.path).is_file():
            pass
        elif old_path and not Path(old_path).is_file() and not (image.path and Path(image.path).is_file()):
            continue
        title = format_flow_display_title(image.index, image.title_hint or flow.image.title_hint)
        return flow.model_copy(update={"image": image, "title": title})
    return None


def run_guide_pipeline(
    docx_path: Path | None = None,
    *,
    content: bytes | None = None,
    filename: str = "",
    recognize: bool = True,
    section_from: int = 2,
    section_to: int = 10,
    on_progress: ProgressCallback | None = None,
    vlm_flow_limit: int | None = None,
) -> GuideParseResult:
    reload_settings()
    settings = get_settings()
    flow_limit = settings.vlm_flow_limit if vlm_flow_limit is None else vlm_flow_limit
    output_root = settings.output_dir / "workflows"
    output_root.mkdir(parents=True, exist_ok=True)

    setup_delivery_logger(settings.log_dir)
    run_log = DeliveryRunLogger()
    source = str(docx_path or settings.guide_docx_path)
    run_log.pipeline_begin(source=source, vlm_flow_limit=flow_limit)

    if on_progress:
        on_progress(0, 0, "正在解析 Word 文档，提取板块与流程图…")

    if content is not None:
        result = parse_guide_docx_from_bytes(
            content,
            filename=filename or settings.guide_docx_name,
            output_dir=output_root,
            section_from=section_from,
            section_to=section_to,
        )
    else:
        path = docx_path or settings.guide_docx_path
        result = parse_guide_docx(
            path,
            output_dir=output_root,
            output_stem=settings.guide_output_stem,
            section_from=section_from,
            section_to=section_to,
        )

    total, skipped_count, to_parse = _count_flowcharts(result.sectors)
    result.total_flowcharts = total
    result.skipped_flowcharts = skipped_count
    effective_limit = _effective_parse_limit(to_parse, flow_limit)

    fatal_errors = [e for e in result.errors if "不存在" in e or "失败" in e]
    if fatal_errors and not result.sectors:
        run_log.pipeline_end(parsed=0, skipped=skipped_count, total=total)
        return result

    client: QwenClient | None = None
    if recognize:
        client = QwenClient()
        if not client.available:
            result.errors.append("未配置 QWEN_API_KEY / OPENAI_API_KEY，已跳过 VLM 流程图识别")
            recognize = False

    if flow_limit > 0 and to_parse > flow_limit:
        result.errors.append(
            f"调试模式：仅解析前 {flow_limit} 个流程（共 {to_parse} 个待解析，其余 VLM 已屏蔽）"
        )

    if on_progress:
        limit_hint = f"，本次仅识别 {effective_limit} 个" if flow_limit > 0 else ""
        on_progress(
            0,
            effective_limit,
            f"Word 提取完成，待 VLM 识别 {to_parse} 张（已跳过 {skipped_count} 张{limit_hint}）",
        )

    previous = load_result(settings)
    if (
        previous
        and getattr(previous, "parse_started_at", "")
        and not getattr(previous, "parse_finished_at", "")
        and (previous.parsed_flowcharts or 0) < to_parse
    ):
        result.parse_started_at = previous.parse_started_at
    else:
        result.parse_started_at = datetime.now().isoformat(timespec="seconds")
    result.parse_finished_at = ""

    parsed_count = 0
    reused_count = 0
    vlm_budget = effective_limit if flow_limit > 0 else to_parse + 1
    sector_results: list[SectorWorkflowResult] = []
    cached_flows = _load_cached_flow_map(settings) if (recognize and settings.guide_parse_use_cache) else {}

    def _flush_progress(*, in_progress: SectorWorkflowResult | None = None) -> None:
        """增量写入磁盘，刷新页面时可看到最新进度。"""
        partial = list(sector_results)
        if in_progress is not None:
            partial.append(in_progress)
        result.parsed_flowcharts = parsed_count
        result.sector_results = partial
        save_result(
            result,
            section_from=section_from,
            section_to=section_to,
            vlm_flow_limit=flow_limit,
        )

    for sector in result.sectors:
        run_log.sector_begin(sector.index, sector.title)
        item = SectorWorkflowResult(sector=sector)
        flows = []
        context = f"板块{sector.index} {sector.title}\n{sector.text_content[:500]}"

        if not sector.images:
            run_log.sector_end(sector.index, sector.title, flow_count=0)
            sector_results.append(item)
            continue

        for image in sector.images:
            flow_title = format_flow_display_title(image.index, image.title_hint)

            if image.skipped:
                run_log.flow_skipped(
                    sector_index=sector.index,
                    flow_title=flow_title,
                    reason=image.skip_reason or "已废弃",
                )
                flows.append(skipped_flow(image))
                continue

            fallback = f"{sector.title} - {image.filename}" if sector.title else image.filename
            cached_flow = _reuse_cached_flow(
                sector_index=sector.index,
                image=image,
                cached=cached_flows,
            )
            if cached_flow:
                flows.append(cached_flow)
                parsed_count += 1
                reused_count += 1
                if on_progress:
                    on_progress(parsed_count, effective_limit, cached_flow.title or flow_title)
                _flush_progress(in_progress=SectorWorkflowResult(sector=sector, flows=list(flows)))
                continue
            if recognize and client and client.available and vlm_budget > 0:
                started = run_log.flow_begin(
                    sector_index=sector.index,
                    sector_title=sector.title,
                    flow_title=flow_title,
                )
                flow = recognize_workflow_flow(
                    image,
                    client=client,
                    context_text=context,
                    fallback_title=fallback,
                )
                run_log.flow_end(
                    sector_index=sector.index,
                    flow_title=flow.title or flow_title,
                    started_at=started,
                    success=flow.success,
                    error=flow.error,
                )
                flows.append(flow)
                parsed_count += 1
                vlm_budget -= 1
                if on_progress:
                    on_progress(parsed_count, effective_limit, flow.title or flow_title)
                _flush_progress(in_progress=SectorWorkflowResult(sector=sector, flows=list(flows)))
            elif recognize and client and client.available and flow_limit > 0:
                run_log.flow_skipped(
                    sector_index=sector.index,
                    flow_title=flow_title,
                    reason=SHIELDED_FLOW_MSG,
                )
                flows.append(placeholder_flow(image, error=SHIELDED_FLOW_MSG))
            else:
                msg = (
                    "未配置 QWEN_API_KEY / OPENAI_API_KEY，已跳过 VLM 识别"
                    if not (client and client.available)
                    else "未启用 VLM 识别"
                )
                run_log.flow_skipped(sector_index=sector.index, flow_title=flow_title, reason=msg)
                flows.append(placeholder_flow(image, error=msg))

        item.flows = flows
        deduped_flows, removed = dedupe_sector_flows(flows)
        if removed:
            result.errors.append(f"板块 {sector.index} 已去除 {removed} 条重复流程")
        item.flows = deduped_flows
        run_log.sector_end(sector.index, sector.title, flow_count=len(deduped_flows))
        sector_results.append(item)

    result.parsed_flowcharts = parsed_count
    result.sector_results = sector_results
    result.parse_finished_at = datetime.now().isoformat(timespec="seconds")
    if reused_count:
        result.errors.append(f"已从缓存复用 {reused_count} 个已成功识别的流程")
    run_log.pipeline_end(parsed=parsed_count, skipped=skipped_count, total=total)
    save_result(
        result,
        section_from=section_from,
        section_to=section_to,
        vlm_flow_limit=flow_limit,
    )
    return result


def load_stored_result() -> GuideParseResult | None:
    """加载已持久化的解析结果。"""
    return load_result()
