"""流程标题过滤：跳过暂停/暂未使用的流程。"""

from __future__ import annotations

import re

from core.form.name_utils import normalize_workflow_name
from schemas.workflow import SectorBlock, WorkflowFlowResult, WorkflowImage

SKIP_MARKERS = ("暂未使用", "暂停")
FLOW_TITLE_RE = re.compile(r"^\d+[\.、]\s*.+")
METADATA_RE = re.compile(r"^(职能科室|职能部门|办公地点|联系电话|使用地点)")
PLAIN_FLOW_TITLE_RE = re.compile(r"[（(](?:已核准|已核对|暂停|暂未使用)[）)]")
BUSINESS_FLOW_SUFFIX_RE = re.compile(r"(借款单|支付单|付款单|报销单|申请单|审批表|备案表|预算表|需求表|登记表|领料单|派车单)$")


def is_metadata_line(text: str) -> bool:
    return bool(METADATA_RE.match((text or "").strip()))


def is_plain_flow_title_line(text: str) -> bool:
    """办事指南中无编号的流程标题，如「集团费用报销单（已核准）」或「文旅集团费用借款单」。"""
    t = (text or "").strip()
    if not t or is_metadata_line(t):
        return False
    if FLOW_TITLE_RE.match(t):
        return True
    if PLAIN_FLOW_TITLE_RE.search(t):
        return True
    if BUSINESS_FLOW_SUFFIX_RE.search(t):
        return True
    return False


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


def _flow_image_quota_key(title_hint: str, *, index: int) -> str:
    from core.form.name_utils import display_form_name

    return display_form_name(title_hint or "") or f"__idx_{index}"


def _flow_dedupe_key(title_hint: str, *, index: int) -> str:
    key = normalize_workflow_name(title_hint)
    return key or f"__idx_{index}"


def parse_flow_titles_from_sector_text(text_content: str) -> list[str]:
    """从板块正文中按出现顺序提取流程标题（去重保留首次）。"""
    titles: list[str] = []
    seen: set[str] = set()
    lines = [ln.strip() for ln in (text_content or "").splitlines()]
    for i, line in enumerate(lines):
        if not line:
            continue
        candidate = ""
        if is_flow_title_line(line):
            candidate = line
        elif is_metadata_line(line) and i > 0:
            prev = lines[i - 1].strip()
            if prev and not is_metadata_line(prev) and not should_skip_flow_title(prev):
                candidate = prev
        if not candidate or should_skip_flow_title(candidate):
            continue
        from core.form.name_utils import display_form_name

        key = display_form_name(candidate)
        if key in seen:
            continue
        seen.add(key)
        titles.append(candidate)
    return titles


def flow_title_document_rank(title_hint: str, ordered_titles: list[str]) -> int:
    """流程标题在办事指南正文中的顺序（0 起）；未匹配则排最后。"""
    from core.form.name_utils import display_form_name, string_similarity

    hint = display_form_name(title_hint or "")
    if not hint or not ordered_titles:
        return 10**9
    for i, raw in enumerate(ordered_titles):
        if display_form_name(raw) == hint:
            return i
    best_idx = 10**9
    best_score = 0.0
    for i, raw in enumerate(ordered_titles):
        score = string_similarity(hint, display_form_name(raw))
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx if best_score >= 0.92 else 10**9


def sort_sector_images_by_document_order(sector: SectorBlock) -> list[WorkflowImage]:
    ordered = parse_flow_titles_from_sector_text(sector.text_content)
    return sorted(
        sector.images,
        key=lambda img: (
            1 if img.skipped else 0,
            flow_title_document_rank(img.title_hint, ordered),
            img.index,
        ),
    )


def select_sector_images_within_limit(sector: SectorBlock, limit: int) -> list[WorkflowImage]:
    """按办事指南正文顺序选取每板块前 limit 个有流程图的流程。"""
    if limit <= 0:
        return [
            img
            for img in sector.images
            if not img.skipped and not should_skip_flow_title(img.title_hint or "")
        ]
    ordered = parse_flow_titles_from_sector_text(sector.text_content)
    eligible = [
        img
        for img in sector.images
        if not img.skipped and not should_skip_flow_title(img.title_hint or "")
    ]
    ranked = sorted(
        eligible,
        key=lambda img: (flow_title_document_rank(img.title_hint, ordered), img.index),
    )
    picked: list[WorkflowImage] = []
    seen_keys: set[str] = set()
    for img in ranked:
        key = _flow_image_quota_key(img.title_hint, index=img.index)
        if key in seen_keys:
            continue
        picked.append(img)
        seen_keys.add(key)
        if len(picked) >= limit:
            break
    return picked


def is_image_within_sector_flow_limit(sector: SectorBlock, image: WorkflowImage, limit: int) -> bool:
    if limit <= 0 or image.skipped:
        return True
    allowed = {_flow_image_quota_key(img.title_hint, index=img.index) for img in select_sector_images_within_limit(sector, limit)}
    return _flow_image_quota_key(image.title_hint, index=image.index) in allowed


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
