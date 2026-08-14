"""流程标题过滤：跳过暂停/暂未使用的流程。"""

from __future__ import annotations

import re

from core.form.name_utils import normalize_workflow_name
from schemas.workflow import SectorBlock, WorkflowFlowResult, WorkflowImage

SKIP_MARKERS = ("暂未使用", "暂停")
FLOW_TITLE_RE = re.compile(r"^\d+[\.、]\s*.+")
METADATA_RE = re.compile(r"^(职能科室|职能部门|办公地点|联系电话|使用地点)")
PLAIN_FLOW_TITLE_RE = re.compile(r"[（(](?:已核准|已核对|暂停|暂未使用)[）)]")


def is_metadata_line(text: str) -> bool:
    return bool(METADATA_RE.match((text or "").strip()))


def is_plain_flow_title_line(text: str) -> bool:
    """办事指南中无编号的流程标题，如「集团费用报销单（已核准）」。"""
    t = (text or "").strip()
    if not t or is_metadata_line(t):
        return False
    if FLOW_TITLE_RE.match(t):
        return True
    return bool(PLAIN_FLOW_TITLE_RE.search(t))


def is_flow_title_line(text: str) -> bool:
    t = (text or "").strip()
    if FLOW_TITLE_RE.match(t):
        return True
    return is_plain_flow_title_line(t)


def format_flow_display_title(index: int, title: str) -> str:
    t = (title or "").strip()
    if not t or is_metadata_line(t):
        return f"{index}. （未命名流程）"
    if is_flow_title_line(t):
        return t
    return f"{index}. {t}"


def should_skip_flow_title(title: str) -> bool:
    text = (title or "").strip()
    return any(marker in text for marker in SKIP_MARKERS)


def skip_reason_for(title: str) -> str:
    text = (title or "").strip()
    if "暂未使用" in text:
        return "已废弃（暂未使用）"
    if "暂停" in text:
        return "已废弃（暂停）"
    return "已废弃"


def _flow_dedupe_key(title_hint: str, *, index: int) -> str:
    key = normalize_workflow_name(title_hint)
    return key or f"__idx_{index}"


def dedupe_sector_images(sector: SectorBlock) -> int:
    """同板块内按归一化标题去重流程图，保留首次出现。"""
    seen: set[str] = set()
    kept: list[WorkflowImage] = []
    removed = 0
    for img in sector.images:
        if img.skipped:
            kept.append(img)
            continue
        key = _flow_dedupe_key(img.title_hint, index=img.index)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(img)
    for i, img in enumerate(kept, start=1):
        img.index = i
    sector.images = kept
    return removed


def dedupe_sector_flows(flows: list[WorkflowFlowResult]) -> tuple[list[WorkflowFlowResult], int]:
    """同板块内按 title_hint 去重解析结果，保留节点数更多的版本。"""
    best: dict[str, WorkflowFlowResult] = {}
    order: list[str] = []
    removed = 0
    for flow in flows:
        if flow.skipped:
            key = f"__skipped_{flow.image.index}"
            best[key] = flow
            order.append(key)
            continue
        key = _flow_dedupe_key(flow.image.title_hint or flow.title, index=flow.image.index)
        if key not in best:
            best[key] = flow
            order.append(key)
            continue
        removed += 1
        existing = best[key]
        existing_score = len(existing.nodes) if existing.success else 0
        flow_score = len(flow.nodes) if flow.success else 0
        if flow_score > existing_score:
            best[key] = flow
    deduped = [best[k] for k in order]
    for i, flow in enumerate(deduped, start=1):
        flow.image.index = i
        hint = (flow.image.title_hint or "").strip()
        if hint:
            flow.title = format_flow_display_title(i, hint)
    return deduped, removed
