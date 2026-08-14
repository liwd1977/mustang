"""AI-02：流程图 VLM 识别与节点 JSON 组装。

产出须与 Word 流程图一致；环节命名、分支绑定等需求加工不在此模块，见 guide_dag_compiler。
"""

from __future__ import annotations

from pathlib import Path

from core.llm.qwen_client import QwenClient
from core.workflow.flow_filter import format_flow_display_title, is_flow_title_line
from core.workflow.node_graph import finalize_workflow_nodes
from schemas.workflow import SectorBlock, WorkflowFlowResult, WorkflowImage, WorkflowNodeItem

ALLOWED_SHAPES = {"圆角矩形", "长方形", "菱形", "波形"}
ALLOWED_NODE_TYPES = {"开始节点", "结束节点", "处理节点", "判断条件", "抄送节点"}

SHAPE_ALIASES = {
    "矩形": "长方形",
    "方形": "长方形",
    "rounded": "圆角矩形",
    "圆角": "圆角矩形",
    "diamond": "菱形",
    "wave": "波形",
    "波浪": "波形",
    "文档": "波形",
}

TYPE_ALIASES = {
    "start": "开始节点",
    "end": "结束节点",
    "process": "处理节点",
    "decision": "判断条件",
    "cc": "抄送节点",
    "抄送": "抄送节点",
    "通知": "抄送节点",
    "判断": "判断条件",
    "处理": "处理节点",
}


def _normalize_shape(raw: str) -> str:
    text = (raw or "").strip()
    for key, val in SHAPE_ALIASES.items():
        if key in text:
            return val
    if text in ALLOWED_SHAPES:
        return text
    if "菱" in text:
        return "菱形"
    if "波" in text or "浪" in text:
        return "波形"
    if "圆角" in text:
        return "圆角矩形"
    return "长方形"


def _normalize_node_type(raw: str, shape: str) -> str:
    text = (raw or "").strip()
    lower = text.lower()
    for key, val in TYPE_ALIASES.items():
        if key in lower or key in text:
            return val
    if text in ALLOWED_NODE_TYPES:
        return text
    if shape == "菱形":
        return "判断条件"
    if shape == "波形":
        return "抄送节点"
    return "处理节点"


def _as_int_list(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _parse_nodes(payload: dict) -> list[WorkflowNodeItem]:
    nodes: list[WorkflowNodeItem] = []
    for item in payload.get("nodes") or []:
        shape = _normalize_shape(str(item.get("shape") or ""))
        node_type = _normalize_node_type(str(item.get("node_type") or item.get("type") or ""), shape)
        try:
            seq = int(item.get("seq"))
        except (TypeError, ValueError):
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        nodes.append(
            WorkflowNodeItem(
                seq=seq,
                shape=shape,
                node_type=node_type,
                content=content,
                prev_seq=_as_int_list(item.get("prev_seq")),
                next_seq=_as_int_list(item.get("next_seq")),
                parallel_seq=_as_int_list(item.get("parallel_seq")),
            )
        )
    nodes.sort(key=lambda n: n.seq)
    return finalize_workflow_nodes(nodes)


def _display_title(image: WorkflowImage, *, raw: str = "", fallback: str = "") -> str:
    hint = (image.title_hint or "").strip()
    if hint and is_flow_title_line(hint):
        return format_flow_display_title(image.index, hint)
    title = (raw or hint or fallback or image.filename).strip()
    return format_flow_display_title(image.index, title)


def recognize_workflow_flow(
    image: WorkflowImage,
    *,
    client: QwenClient,
    context_text: str = "",
    fallback_title: str = "",
) -> WorkflowFlowResult:
    try:
        payload = client.recognize_flowchart(
            Path(image.path),
            context_text=context_text,
        )
        nodes = _parse_nodes(payload)
        if not nodes:
            return WorkflowFlowResult(
                image=image,
                title=_display_title(image, raw=str(payload.get("title") or ""), fallback=fallback_title),
                success=False,
                error="解析失败：未识别到有效节点",
            )
        return WorkflowFlowResult(
            image=image,
            title=_display_title(image, raw=str(payload.get("title") or ""), fallback=fallback_title),
            success=True,
            confidence=float(payload.get("confidence") or 0.0),
            nodes=nodes,
        )
    except Exception as exc:
        return WorkflowFlowResult(
            image=image,
            title=_display_title(image, fallback=fallback_title),
            success=False,
            error=f"解析失败：{exc}",
        )


def recognize_sector_flows(
    sector: SectorBlock,
    *,
    client: QwenClient | None = None,
) -> list[WorkflowFlowResult]:
    if not sector.images:
        return []

    qwen = client or QwenClient()
    results: list[WorkflowFlowResult] = []
    context = f"板块{sector.index} {sector.title}\n{sector.text_content[:500]}"
    for image in sector.images:
        fallback = f"{sector.title} - {image.filename}" if sector.title else image.filename
        results.append(
            recognize_workflow_flow(
                image,
                client=qwen,
                context_text=context,
                fallback_title=fallback,
            )
        )
    return results


def skipped_flow(image: WorkflowImage) -> WorkflowFlowResult:
    reason = image.skip_reason or "已废弃（标题含暂停或暂未使用）"
    return WorkflowFlowResult(
        image=image,
        title=_display_title(image),
        success=False,
        skipped=True,
        error=reason,
    )


def placeholder_flow(image: WorkflowImage, *, error: str) -> WorkflowFlowResult:
    return WorkflowFlowResult(
        image=image,
        title=_display_title(image),
        success=False,
        error=error,
    )
