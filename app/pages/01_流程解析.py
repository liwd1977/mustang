"""流程解析页：办事指南 Word + 工作流 VLM 识别。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config.settings import reload_settings
from core.logging.delivery_logger import get_session_log_path
from core.form.name_utils import normalize_workflow_name, workflow_name_matches_query
from core.ui.progress_timing import should_show_timing, timing_label
from core.workflow.flow_filter import dedupe_sector_flows, format_flow_display_title
from core.workflow.pipeline import load_stored_result, run_guide_pipeline
from core.workflow.result_store import get_store_meta, get_store_path, is_result_stale, normalize_parse_result, save_result
from core.workflow.vlm_recognizer import skipped_flow
from schemas.workflow import GuideParseResult, SectorBlock, SectorWorkflowResult, WorkflowFlowResult, WorkflowNodeItem

settings = reload_settings()
doc_path = settings.guide_docx_path

st.set_page_config(page_title="流程解析", layout="wide")
st.title("流程解析")
_caption = "AI-01 Word 解析 + AI-02 VLM 流程图识别（第二~十板块）"
if settings.vlm_flow_limit > 0:
    _caption += f" · 调试模式：VLM 仅解析前 {settings.vlm_flow_limit} 个流程"
st.caption(_caption)

if "parse_result" not in st.session_state:
    st.session_state.parse_result = load_stored_result()
elif st.session_state.parse_result is not None:
    st.session_state.parse_result = normalize_parse_result(st.session_state.parse_result)
if "result_from_store" not in st.session_state:
    st.session_state.result_from_store = st.session_state.parse_result is not None

# 后台任务运行时从磁盘刷新进度
if st.session_state.result_from_store:
    _fresh = load_stored_result()
    if _fresh is not None:
        st.session_state.parse_result = normalize_parse_result(_fresh)

_cached = st.session_state.parse_result
if _cached and st.session_state.get("progress_done") is None and st.session_state.result_from_store:
    _to_parse = _cached.total_flowcharts - _cached.skipped_flowcharts
    _done = _cached.parsed_flowcharts or 0
    st.session_state.progress_ratio = (_done / _to_parse) if _to_parse else 0.0
    if _to_parse and _done >= _to_parse:
        st.session_state.progress_text = "解析完成"
    elif _done > 0:
        st.session_state.progress_text = f"VLM 识别进度：{_done}/{_to_parse}（缓存）"
    else:
        st.session_state.progress_text = "已加载缓存"
    st.session_state.progress_done = _done
    st.session_state.progress_total = _to_parse
    st.session_state.progress_skip = _cached.skipped_flowcharts
    if _cached.parse_started_at:
        st.session_state.progress_started_at = getattr(_cached, "parse_started_at", "") or ""
    if getattr(_cached, "parse_finished_at", ""):
        st.session_state.progress_finished_at = getattr(_cached, "parse_finished_at", "") or ""
    st.session_state.progress_detail = f"缓存于 {_cached.saved_at}" if _cached.saved_at else "已加载本地缓存"


class _ProgressPanel:
    """主区域 + 侧边栏双进度条，解析过程中同步刷新。"""

    def __init__(self) -> None:
        self.ratio = float(st.session_state.get("progress_ratio", 0.0))
        self.text = st.session_state.get("progress_text", "等待开始…")
        self.done = st.session_state.get("progress_done")
        self.total = st.session_state.get("progress_total")
        self.skip = st.session_state.get("progress_skip")
        self.detail = st.session_state.get("progress_detail", "")
        self.timing = st.session_state.get("progress_timing", "")

    def _sync_timing_from_result(self, result: GuideParseResult | None) -> None:
        if not result:
            return
        to_parse = (result.total_flowcharts or 0) - (result.skipped_flowcharts or 0)
        if not should_show_timing(to_parse):
            self.timing = ""
            return
        started_at = getattr(result, "parse_started_at", "") or ""
        finished_at = getattr(result, "parse_finished_at", "") or ""
        running = bool(to_parse and (result.parsed_flowcharts or 0) < to_parse and not finished_at)
        label = timing_label(
            started_at=started_at or None,
            finished_at=finished_at or None,
            running=running,
        )
        if label:
            self.timing = label
            st.session_state.progress_timing = label

    def render_main(self) -> None:
        with st.container(border=True):
            st.markdown("### 解析进度")
            self.main_bar = st.progress(self.ratio, text=self.text)
            p_col1, p_col2, p_col3, p_col4 = st.columns(4)
            self.main_done = p_col1.empty()
            self.main_total = p_col2.empty()
            self.main_skip = p_col3.empty()
            self.main_timing = p_col4.empty()
            self.main_detail = st.empty()
            self._fill_metrics(self.main_done, self.main_total, self.main_skip, self.main_timing)
            if self.detail:
                self.main_detail.info(self.detail)

    def render_sidebar(self) -> None:
        st.markdown("**实时进度**")
        self.side_bar = st.progress(self.ratio, text=self.text)
        self.side_timing = st.empty()
        self.side_detail = st.empty()
        if self.timing:
            self.side_timing.caption(self.timing)
        if self.detail:
            self.side_detail.caption(self.detail)

    def _fill_metrics(self, done_slot, total_slot, skip_slot, timing_slot) -> None:
        if self.done is not None:
            done_slot.metric("已解析", self.done)
            total_slot.metric("待解析总数", self.total or 0)
            skip_slot.metric("已跳过", self.skip or 0)
        else:
            done_slot.metric("已解析", "—")
            total_slot.metric("待解析总数", "—")
            skip_slot.metric("已跳过", "—")
        if self.timing:
            timing_slot.metric("耗时", self.timing.replace("已耗时 ", "").replace("总耗时 ", ""))
        else:
            timing_slot.metric("耗时", "—")

    def update(
        self,
        done: int,
        total: int,
        title: str,
        *,
        skipped: int = 0,
        started_at: str | None = None,
        finished_at: str | None = None,
        running: bool | None = None,
    ) -> None:
        if total > 0:
            ratio = min(done / total, 1.0)
            bar_text = f"VLM 识别进度：{done}/{total}"
        else:
            ratio = 0.0
            bar_text = "Word 文档解析中…"

        timing = ""
        if should_show_timing(total):
            is_running = running if running is not None else (total > 0 and done < total)
            timing = timing_label(
                started_at=started_at or st.session_state.get("progress_started_at"),
                finished_at=finished_at,
                running=is_running,
            )

        bar_display = f"{bar_text} · {timing}" if timing else bar_text
        self.main_bar.progress(ratio, text=bar_display)
        self.side_bar.progress(ratio, text=bar_display)
        self.main_done.metric("已解析", done)
        self.main_total.metric("待解析总数", total)
        self.main_skip.metric("已跳过", skipped)
        if timing:
            self.main_timing.metric("耗时", timing.replace("已耗时 ", "").replace("总耗时 ", ""))
            self.side_timing.caption(timing)
            self.timing = timing
        self.main_detail.info(f"**当前：** {title}")
        self.side_detail.caption(f"当前：{title}")

        st.session_state.progress_ratio = ratio
        st.session_state.progress_text = bar_text
        st.session_state.progress_done = done
        st.session_state.progress_total = total
        st.session_state.progress_skip = skipped
        st.session_state.progress_detail = title
        st.session_state.progress_timing = timing


progress_panel = _ProgressPanel()
if _cached:
    progress_panel._sync_timing_from_result(_cached)
progress_panel.render_main()


@st.dialog("流程 JSON", width="large")
def _show_flow_json_dialog(title: str, json_text: str) -> None:
    st.markdown(f"**{title}**")
    st.code(json_text, language="json")


@st.dialog("流程图", width="large")
def _show_flow_image_dialog(title: str, image_path: str) -> None:
    st.markdown(f"**{title}**")
    path = Path(image_path)
    if path.is_file():
        st.image(str(path), use_container_width=True)
        st.caption(str(path))
    else:
        st.warning("流程图文件不存在或路径无效")


def _flow_heading(flow: WorkflowFlowResult) -> str:
    hint = (flow.image.title_hint or "").strip()
    if hint:
        return format_flow_display_title(flow.image.index, hint)
    if flow.title:
        return flow.title
    return format_flow_display_title(flow.image.index, "")


def _flow_matches_search(flow: WorkflowFlowResult, query: str) -> bool:
    if not (query or "").strip():
        return True
    for text in (flow.image.title_hint, flow.title, _flow_heading(flow)):
        if workflow_name_matches_query(query, text):
            return True
    return False


def _align_flows_to_images(sector: SectorBlock, parsed_flows: list[WorkflowFlowResult]) -> list[WorkflowFlowResult]:
    """将已解析流程与当前 Word 提取的图片列表对齐（去重后索引变化时仍能匹配）。"""
    if not sector.images:
        return []
    if not parsed_flows:
        return [
            WorkflowFlowResult(
                image=img,
                title=format_flow_display_title(img.index, img.title_hint),
                success=False,
                skipped=img.skipped,
                error=img.skip_reason or "尚未解析",
            )
            for img in sector.images
        ]

    by_title: dict[str, WorkflowFlowResult] = {}
    by_path: dict[str, WorkflowFlowResult] = {}
    for flow in parsed_flows:
        title_key = normalize_workflow_name(flow.image.title_hint)
        if title_key:
            by_title[title_key] = flow
        if flow.image.path:
            by_path[flow.image.path.replace("\\", "/")] = flow
        if flow.image.filename:
            by_path[flow.image.filename] = flow

    aligned: list[WorkflowFlowResult] = []
    for img in sector.images:
        if img.skipped:
            aligned.append(skipped_flow(img))
            continue
        title_key = normalize_workflow_name(img.title_hint)
        path_key = (img.path or "").replace("\\", "/")
        matched = (
            (title_key and by_title.get(title_key))
            or (path_key and by_path.get(path_key))
            or (img.filename and by_path.get(img.filename))
        )
        if matched:
            heading = format_flow_display_title(img.index, img.title_hint or matched.image.title_hint)
            aligned.append(matched.model_copy(update={"image": img, "title": heading}))
        else:
            aligned.append(
                WorkflowFlowResult(
                    image=img,
                    title=format_flow_display_title(img.index, img.title_hint),
                    success=False,
                    error="尚未解析",
                )
            )
    return aligned


def _build_display_items(result: GuideParseResult) -> list[SectorWorkflowResult]:
    parsed_map = {item.sector.index: item for item in (result.sector_results or [])}
    items: list[SectorWorkflowResult] = []
    for sector in result.sectors:
        parsed = parsed_map.get(sector.index)
        flows = _align_flows_to_images(sector, parsed.flows if parsed else [])
        items.append(
            SectorWorkflowResult(
                sector=sector,
                flows=flows,
                error=parsed.error if parsed else "",
            )
        )
    return items


def _visible_flows_for_item(item: SectorWorkflowResult, search_query: str) -> list[WorkflowFlowResult]:
    flows = item.flows
    if not flows:
        flows = _align_flows_to_images(item.sector, [])
    deduped_flows, _removed = dedupe_sector_flows(flows)
    return [f for f in deduped_flows if _flow_matches_search(f, search_query)]


def _flow_identity(flow: WorkflowFlowResult) -> tuple[str, str]:
    return (flow.title or "", flow.image.path or "")


def _apply_flow_node_edits(
    result: GuideParseResult,
    flow: WorkflowFlowResult,
    edits: dict[int, str],
) -> bool:
    target_title, target_path = _flow_identity(flow)
    changed = False
    for item in result.sector_results:
        for idx, candidate in enumerate(item.flows):
            if _flow_identity(candidate) != (target_title, target_path):
                continue
            new_nodes: list[WorkflowNodeItem] = []
            for node in candidate.nodes:
                new_content = edits.get(node.seq)
                if new_content is not None and new_content != (node.content or ""):
                    new_nodes.append(node.model_copy(update={"content": new_content}))
                    changed = True
                else:
                    new_nodes.append(node)
            item.flows[idx] = candidate.model_copy(update={"nodes": new_nodes})
    return changed


def _render_flow_item(flow: WorkflowFlowResult, *, dialog_key: str) -> None:
    heading = _flow_heading(flow)
    st.markdown(f"##### {heading}")

    action_col, _ = st.columns([1, 3])
    with action_col:
        if not flow.skipped and Path(flow.image.path).is_file():
            if st.button("查看流程图", key=f"img_{dialog_key}", type="secondary"):
                _show_flow_image_dialog(heading, flow.image.path)
        elif flow.skipped:
            st.caption("已废弃，无流程图")

    if flow.skipped:
        st.warning(flow.error or "已废弃")
    elif flow.success:
        st.caption(f"节点 {len(flow.nodes)} 个 · 置信度 {flow.confidence:.0%}")
        with st.expander("节点列表", expanded=False):
            for node in flow.nodes:
                st.markdown(
                    f"- **{node.seq}.** [{node.node_type}] {node.content} "
                    f"（{node.shape}）"
                )
        with st.expander("调试：覆盖本地 parse 缓存", expanded=False):
            st.caption(
                "仅用于开发对照，非正式链路。"
                "生产上 parse_result 以 VLM 对 Word 图的识别为准；"
                "环节命名、分支绑定等需求加工由「流程写入」侧的编译器实现。"
            )
            edits: dict[int, str] = {}
            for node in flow.nodes:
                edits[node.seq] = st.text_input(
                    f"seq {node.seq} · {node.node_type}",
                    value=node.content or "",
                    key=f"node_edit_{dialog_key}_{node.seq}",
                )
            if st.button("保存到本地缓存", key=f"save_nodes_{dialog_key}"):
                result: GuideParseResult | None = st.session_state.get("parse_result")
                if result is None:
                    st.error("无解析结果可保存")
                elif _apply_flow_node_edits(result, flow, edits):
                    save_result(
                        result,
                        section_from=result.section_from,
                        section_to=result.section_to,
                        vlm_flow_limit=result.vlm_flow_limit,
                        settings=settings,
                    )
                    st.session_state.parse_result = normalize_parse_result(result)
                    st.session_state.result_from_store = True
                    st.success("节点修改已写入 parse_result.json，请到「流程写入」一键写入。")
                    st.rerun()
                else:
                    st.info("内容无变化，未写入。")
    elif "调试模式" in (flow.error or ""):
        st.warning(flow.error)
    else:
        st.error(flow.error or "解析失败")

    if st.button("查看 JSON", key=f"json_{dialog_key}", type="secondary"):
        _show_flow_json_dialog(heading, flow.to_json_text())


def _render_sector_result(
    item: SectorWorkflowResult,
    *,
    search_query: str = "",
    visible_flows: list[WorkflowFlowResult] | None = None,
    expanded: bool = False,
) -> None:
    sector = item.sector
    header = f"{sector.index}. {sector.title or '（无标题）'}"
    with st.expander(header, expanded=expanded):
        if not sector.images:
            st.warning("该板块未检测到流程图图片")
            return

        if item.error and not item.flows:
            st.error(item.error)

        if visible_flows is None:
            flows = item.flows or _align_flows_to_images(sector, [])
            deduped_flows, removed = dedupe_sector_flows(flows)
            if removed:
                st.caption(f"已折叠 {removed} 条重复流程（同标题保留节点数最多版本）")
            visible_flows = [f for f in deduped_flows if _flow_matches_search(f, search_query)]

        if search_query and not visible_flows:
            return

        st.markdown(f"**流程列表（{len(visible_flows)} 个）**")
        for idx, flow in enumerate(visible_flows):
            if idx > 0:
                st.divider()
            _render_flow_item(flow, dialog_key=f"s{sector.index}_f{idx}")


def _collect_flow_status_lists(result: GuideParseResult) -> tuple[list[dict], list[dict]]:
    """汇总全部板块的跳过 / 失败流程清单。"""
    skip_rows: list[dict] = []
    fail_rows: list[dict] = []
    for item in _build_display_items(result):
        sector_label = f"{item.sector.index}. {item.sector.title or '（无标题）'}"
        flows = item.flows or _align_flows_to_images(item.sector, [])
        for flow in flows:
            heading = _flow_heading(flow)
            if flow.skipped:
                reason = (flow.error or flow.image.skip_reason or "已废弃").strip()
                skip_rows.append(
                    {"板块": sector_label, "流程名称": heading, "跳过原因": reason or "—"}
                )
            elif not flow.success:
                fail_rows.append(
                    {
                        "板块": sector_label,
                        "流程名称": heading,
                        "失败提示": (flow.error or "解析失败").strip() or "解析失败",
                    }
                )
    return skip_rows, fail_rows


def _render_flow_status_lists(skip_rows: list[dict], fail_rows: list[dict]) -> None:
    st.divider()
    st.subheader("跳过清单")
    if skip_rows:
        st.dataframe(skip_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("无跳过流程")

    st.subheader("失败清单")
    if fail_rows:
        st.dataframe(fail_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("无解析失败流程")


def _update_progress(done: int, total: int, title: str, *, skipped: int = 0) -> None:
    running = total > 0 and done < total
    progress_panel.update(
        done,
        total,
        title,
        skipped=skipped,
        started_at=st.session_state.get("progress_started_at"),
        finished_at=st.session_state.get("progress_finished_at") or None,
        running=running,
    )


store_meta = get_store_meta()

with st.sidebar:
    progress_panel.render_sidebar()
    st.divider()
    st.header("数据源")
    st.markdown(f"**文档路径**\n\n`{doc_path}`")
    if doc_path.is_file():
        st.success("文档已就绪")
    else:
        st.error("文档未找到，请将办事指南放入 data/ 目录")

    st.divider()
    st.subheader("存储结果")
    if store_meta.get("exists") and store_meta.get("valid"):
        st.success("已有缓存")
        if store_meta.get("saved_at"):
            st.caption(f"保存时间：{store_meta['saved_at']}")
        st.caption(f"板块 {store_meta.get('sector_count', 0)} · 流程图 {store_meta.get('total_flowcharts', 0)}")
        if store_meta.get("stale"):
            st.warning("源文档已更新，建议重新解析")
    else:
        st.info("尚无缓存，请先解析")

    st.divider()
    enable_vlm = st.toggle("启用 VLM 流程图识别", value=settings.llm_configured, disabled=not settings.llm_configured)
    if settings.llm_configured:
        st.caption("API Key 已加载（支持 QWEN_API_KEY / OPENAI_API_KEY）")
    else:
        st.caption("请配置环境变量 QWEN_API_KEY 或 OPENAI_API_KEY")
    if settings.vlm_flow_limit > 0:
        st.warning(f"调试模式：VLM 仅解析前 {settings.vlm_flow_limit} 个流程")
    else:
        st.caption("VLM 解析范围：全部流程（无数量限制）")

    section_from = st.number_input("起始板块", min_value=1, max_value=10, value=2)
    section_to = st.number_input("结束板块", min_value=1, max_value=10, value=10)

    start_clicked = st.button(
        "重新解析",
        type="primary",
        use_container_width=True,
        disabled=not doc_path.is_file(),
        help="重新执行 Word 提取与 VLM 识别，并覆盖已存储结果",
    )

if start_clicked:
    from datetime import datetime

    st.session_state.result_from_store = False
    st.session_state.progress_ratio = 0.0
    st.session_state.progress_done = 0
    st.session_state.progress_text = "准备开始…"
    st.session_state.progress_started_at = datetime.now().isoformat(timespec="seconds")
    st.session_state.progress_finished_at = ""
    try:
        skip_holder = {"skipped": 0}

        def _on_progress(done: int, total: int, title: str) -> None:
            if done == 0 and "已跳过" in title:
                import re

                match = re.search(r"已跳过\s*(\d+)", title)
                if match:
                    skip_holder["skipped"] = int(match.group(1))
            _update_progress(done, total, title, skipped=skip_holder["skipped"])

        _update_progress(0, 0, "正在解析 Word 文档…")
        result = run_guide_pipeline(
            docx_path=doc_path,
            recognize=enable_vlm,
            section_from=int(section_from),
            section_to=int(section_to),
            on_progress=_on_progress,
            vlm_flow_limit=0,
            per_sector_flow_limit=0,
        )
        skip_holder["skipped"] = result.skipped_flowcharts
        to_parse = result.total_flowcharts - result.skipped_flowcharts
        _update_progress(
            result.parsed_flowcharts,
            to_parse,
            f"解析完成：成功 {sum(1 for s in result.sector_results for f in s.flows if f.success)} 个",
            skipped=result.skipped_flowcharts,
        )
        progress_panel.main_bar.progress(1.0, text="解析完成")
        progress_panel.side_bar.progress(1.0, text="解析完成")
        st.session_state.parse_result = result
        st.session_state.result_from_store = False
        st.session_state.progress_text = "解析完成"
        st.session_state.progress_finished_at = getattr(result, "parse_finished_at", "") or ""
        if getattr(result, "parse_finished_at", ""):
            progress_panel.update(
                result.parsed_flowcharts,
                to_parse,
                f"解析完成：成功 {sum(1 for s in result.sector_results for f in s.flows if f.success)} 个",
                skipped=result.skipped_flowcharts,
                started_at=getattr(result, "parse_started_at", "") or st.session_state.get("progress_started_at"),
                finished_at=getattr(result, "parse_finished_at", "") or None,
                running=False,
            )
    except Exception as exc:
        progress_panel.main_detail.error(f"解析失败：{exc}")
        st.error(f"解析失败：{exc}")

result: GuideParseResult | None = st.session_state.parse_result

if result is None:
    st.info("暂无解析结果。请在左侧点击「重新解析」，或确认已有缓存文件存在。")
    st.markdown(f"当前文档：`{doc_path}`")
    st.caption(f"缓存路径：`{get_store_path()}`")
    st.stop()

if st.session_state.get("result_from_store") and not start_clicked:
    stale = is_result_stale(result)
    to_parse = (result.total_flowcharts or 0) - (result.skipped_flowcharts or 0)
    debug_cache = (result.vlm_flow_limit or 0) > 0 and settings.vlm_flow_limit <= 0
    partial_cache = to_parse > 0 and (result.parsed_flowcharts or 0) < to_parse
    if debug_cache:
        st.warning(
            f"当前缓存为调试模式结果（仅解析 {result.parsed_flowcharts} 个流程），"
            "请点击「重新解析」以生成全部流程 JSON。"
        )
    elif partial_cache:
        st.warning(
            f"解析未完成（{result.parsed_flowcharts}/{to_parse}），"
            "请点击「重新解析」继续完成全部流程。"
        )
    elif stale:
        if settings.guide_parse_use_cache:
            st.warning("已加载本地缓存，但 Word 源文档已变更，建议点击「重新解析」更新。")
        else:
            st.info(
                "已加载本地缓存，但 Word 源文档已变更。"
                "开发模式下「流程写入」会自动重新解析；也可在此提前点击「重新解析」查看结果。"
            )
    else:
        saved = result.saved_at or store_meta.get("saved_at") or "未知"
        st.success(f"已加载本地缓存结果（保存于 {saved}），无需重新解析。")

if result.errors and not result.sectors:
    for err in result.errors:
        st.error(err)
    st.stop()

if result.errors:
    for err in result.errors:
        st.warning(err)

st.divider()
st.subheader("解析结果")

_done = result.parsed_flowcharts or 0
skip_rows, fail_rows = _collect_flow_status_lists(result)
failed_count = len(fail_rows)
success_count = sum(1 for s in result.sector_results for f in s.flows if f.success)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("解析板块数", len(result.sectors))
m2.metric("流程图总数", result.total_flowcharts or sum(len(s.sector.images) for s in result.sector_results))
m3.metric("VLM 已解析", _done)
m4.metric("已跳过", result.skipped_flowcharts)
m5.metric("解析失败数", failed_count)
st.caption(f"VLM 成功识别：{success_count} 个")

st.markdown(f"来源文件：`{result.source_file}`")
st.caption(f"缓存文件：`{get_store_path()}`")
if result.saved_at:
    st.caption(f"保存时间：`{result.saved_at}`")
log_path = get_session_log_path()
if log_path and not st.session_state.get("result_from_store"):
    st.caption(f"运行日志：`{log_path}`")

st.divider()

search_query = st.text_input(
    "搜索流程",
    placeholder="例如：样车合同备案、费用报销",
    key="flow_search_query",
)

display_items = _build_display_items(result)
search_active = bool(search_query.strip())

if search_active:
    matched_sections: list[tuple[SectorWorkflowResult, list[WorkflowFlowResult]]] = []
    for item in display_items:
        visible = _visible_flows_for_item(item, search_query)
        if visible:
            matched_sections.append((item, visible))
    if not matched_sections:
        st.warning(f"未找到匹配「{search_query}」的流程")
    else:
        flow_total = sum(len(v) for _, v in matched_sections)
        st.success(f"在 {len(matched_sections)} 个板块中找到 {flow_total} 个流程")
        for item, visible in matched_sections:
            _render_sector_result(
                item,
                search_query=search_query,
                visible_flows=visible,
                expanded=True,
            )
else:
    for item in display_items:
        _render_sector_result(item, search_query="", expanded=False)

_render_flow_status_lists(skip_rows, fail_rows)

with st.expander("完整 JSON 快照", expanded=False):
    st.json(result.model_dump())

_partial = (result.total_flowcharts or 0) - (result.skipped_flowcharts or 0)
if (
    st.session_state.get("result_from_store")
    and not start_clicked
    and _partial > 0
    and (result.parsed_flowcharts or 0) < _partial
    and not getattr(result, "parse_finished_at", "")
):
    import time

    time.sleep(3)
    st.rerun()
