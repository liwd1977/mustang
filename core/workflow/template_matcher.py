"""模板表 C 列 → 办事指南流程匹配与去重。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.config.settings import Settings, get_settings
from core.form.form_store import load_form, search_forms
from core.form.name_utils import display_form_name, normalize_for_search, string_similarity, strip_status_suffix
from core.form.task_table import _split_form_cell, load_pending_write_template_rows
from core.form.template_table import TemplateTableRow
from core.workflow.flow_filter import parse_flow_titles_from_sector_text, should_skip_flow_title
from core.workflow.result_store import load_result
from schemas.workflow import GuideParseResult, SectorBlock, WorkflowFlowResult

MATCH_THRESHOLD = 0.82
FORM_MATCH_THRESHOLD = 0.82

# 模板表板块标签 → 办事指南板块 title 关键词
SECTOR_HINTS: dict[str, list[str]] = {
    "野马集团": ["野马集团", "二线"],
    "金融板块": ["金融"],
    "文旅板块": ["文旅"],
    "外贸板块": ["外贸"],
    "腾宇通达": ["腾宇通达"],
    "晟诚立通": ["晟诚立通"],
    "鼎泰伟业": ["鼎泰"],
    "全集团通用表单、流程（大致相同，不同板块微调）": ["野马集团", "二线"],
    "晟诚立通与鼎泰伟业表单、流程全相同，只有名称变化": ["晟诚立通"],
    "外贸和文旅同表同流程": [],
}

FLOW_SUFFIX_STRIP_RE = re.compile(r"[（(](?:借款|还款|已核准|已核对)[）)]")

# 任务表 C 列 / 写入目标 → 办事指南 VLM 流程图标题（精确映射优先于模糊匹配）
VLM_FLOW_TITLE_OVERRIDES: dict[str, str] = {
    "外贸集团：布尔津矿业费用报销单": "外贸集团：布尔津矿业费用报销（支付）单",
    "外贸集团：布尔津矿业费用支付单": "外贸集团：布尔津矿业费用报销（支付）单",
    "外贸板块费用报销单": "外贸板块费用报销单",
    "文旅集团文化公司费用借款单": "文旅集团费用借款单",
    "文旅集团文化公司费用支付单": "文旅集团费用支付单",
    "文旅集团文化公司费用报销单": "文旅集团费用报销单",
    "文旅集团审计档案借阅申请表": "文旅集团财务档案借阅申请表",
}

# 任务表 C 列流程名纠正（与办事指南 / 目标流程一致）
FLOW_TITLE_CORRECTIONS: dict[str, str] = {
    "文旅集团审计档案借阅申请表": "文旅集团财务档案借阅申请表",
}


def correct_flow_title(flow_title: str) -> str:
    text = (flow_title or "").strip()
    return FLOW_TITLE_CORRECTIONS.get(text, text)

# 模板表 C 列 → 办事指南流程标题（Excel 含补充文字时的明确映射，写入 _match_row_to_guide）
BACKEND_FLOW_GUIDE_OVERRIDES: dict[str, str] = dict(VLM_FLOW_TITLE_OVERRIDES)

# 多行 C 列合并为同一指南流程时，优先采用与指南流程类型一致的 C 列对应表单
GUIDE_FLOW_PREFERRED_BACKEND: dict[str, str] = {
    "集团费用报销单": "集团费用报销单",
    "外贸集团费用支付单": "外贸集团费用支付单",
    "文旅集团采购借款单": "文旅集团采购借款单",
}

# 指南流程 → 指定 OA 表单名（模板表 B 列原系统表单名称）
GUIDE_FLOW_PREFERRED_FORM: dict[str, str] = {
    "外贸集团费用支付单": "外贸集团费用报销（支付）单",
    "文旅集团采购借款单": "文旅集团采购借款单",
}

# 显示表单名 → 已提取 OA 表单存储名（本地 forms 目录 slug 不一致时的别名）
FORM_LOAD_ALIASES: dict[str, str] = {
    "文旅集团采购借款单": "文旅集团（领）借款单",
}

# 多行 C 列合并为同一指南流程时，融合多个 OA 表单字段（取并集去重）
GUIDE_FLOW_MERGED_FORMS: dict[str, list[str]] = {
    "集团资金调拨单": ["集团资金调拨单（借款）", "集团资金调拨单（还款）"],
}


@dataclass
class TemplateRowMatch:
    row: TemplateTableRow
    oa_form_slugs: list[str] = field(default_factory=list)
    oa_forms_display: str = ""
    is_new_form: bool = False
    form_match_score: float = 0.0
    guide_sector_index: int = 0
    guide_sector_title: str = ""
    flowchart_title: str = ""
    target_key: str = ""
    skip_reason: str = ""

    @property
    def matched(self) -> bool:
        """是否匹配到 OA 表单（非新增）。"""
        return bool(self.oa_form_slugs) and not self.is_new_form


@dataclass
class TemplateGenerationTarget:
    target_key: str
    guide_sector_index: int
    guide_sector_title: str
    guide_title: str
    workflow_key: str
    form_slug: str
    app_display_name: str
    is_new_form: bool = False
    form_slugs: list[str] = field(default_factory=list)
    source_rows: list[TemplateTableRow] = field(default_factory=list)
    backend_flow_labels: list[str] = field(default_factory=list)
    enabled: bool = True
    skip_reason: str = ""


@dataclass
class TemplateMatchReport:
    rows: list[TemplateRowMatch] = field(default_factory=list)
    targets: list[TemplateGenerationTarget] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(1 for r in self.rows if r.matched)

    @property
    def new_form_count(self) -> int:
        return sum(1 for r in self.rows if r.is_new_form)

    @property
    def enabled_targets(self) -> list[TemplateGenerationTarget]:
        return [t for t in self.targets if t.enabled]


def ai_app_display_name(guide_title: str) -> str:
    base = display_form_name(guide_title)
    return base if base.startswith("AI_") else f"AI_{base}"


def template_registry_name(target_key: str) -> str:
    return f"AI_{target_key}"


def flow_match_hints(backend_flow: str) -> list[str]:
    text = (backend_flow or "").strip()
    hints = [text, strip_status_suffix(text), display_form_name(text)]
    stripped = FLOW_SUFFIX_STRIP_RE.sub("", text).strip()
    if stripped and stripped != text:
        hints.extend([stripped, display_form_name(stripped), f"{stripped}（已核准）"])
    if "资金调拨" in text:
        hints.append("集团资金调拨单")
        hints.append("集团资金调拨单（已核准）")
    return list(dict.fromkeys(h for h in hints if h))


def _sector_title_matches(sector_title: str, template_sector: str) -> bool:
    st = normalize_for_search(sector_title)
    hints = SECTOR_HINTS.get(template_sector)
    if hints is not None:
        if not hints:
            return True
        return any(normalize_for_search(h) in st or st in normalize_for_search(h) for h in hints)
    ts = normalize_for_search(template_sector)
    if ts in st or st in ts:
        return True
    return string_similarity(template_sector, sector_title) >= 0.75


def _backend_flow_implies_sector(backend_flow: str) -> list[str]:
    """C 列名称本身可暗示板块（如「文旅集团…」「外贸板块…」）。"""
    hints: list[str] = []
    if "文旅" in backend_flow:
        hints.append("文旅")
    if "外贸" in backend_flow:
        hints.append("外贸")
    if "金融" in backend_flow:
        hints.append("金融")
    if "晟诚立通" in backend_flow:
        hints.append("晟诚立通")
    if "腾宇通达" in backend_flow:
        hints.append("腾宇通达")
    if "集团" in backend_flow and "文旅" not in backend_flow and "外贸" not in backend_flow:
        hints.extend(["野马集团", "二线"])
    return hints


def _candidate_sectors(
    sectors: list[SectorBlock],
    template_sector: str,
    backend_flow: str,
) -> list[SectorBlock]:
    flow_hints = _backend_flow_implies_sector(backend_flow)
    if flow_hints and not SECTOR_HINTS.get(template_sector):
        matched = [
            s
            for s in sectors
            if any(normalize_for_search(h) in normalize_for_search(s.title) for h in flow_hints)
        ]
        if matched:
            return matched

    matched = [s for s in sectors if _sector_title_matches(s.title, template_sector)]
    if matched:
        return matched

    if flow_hints:
        return [
            s
            for s in sectors
            if any(normalize_for_search(h) in normalize_for_search(s.title) for h in flow_hints)
        ]
    return list(sectors)


def _score_guide_title(hints: list[str], title: str) -> float:
    best = 0.0
    for hint in hints:
        score = string_similarity(hint, title)
        if score > best:
            best = score
    return best


def _resolve_guide_title_by_name(
    guide_name: str,
    sectors: list[SectorBlock],
    template_sector: str,
    backend_flow: str,
) -> tuple[int, str, str]:
    """在候选板块中按办事指南流程名定位标题（含状态后缀变体）。"""
    target_norm = display_form_name(guide_name)
    candidates = _candidate_sectors(sectors, template_sector, backend_flow)
    for sector in candidates:
        for image in sector.images:
            title = (image.title_hint or "").strip()
            if not title or image.skipped or should_skip_flow_title(title):
                continue
            if display_form_name(title) == target_norm:
                return sector.index, sector.title, title
        for title in parse_flow_titles_from_sector_text(sector.text_content):
            if display_form_name(title) == target_norm:
                return sector.index, sector.title, title
    return 0, "", ""


def _match_row_to_guide(
    row: TemplateTableRow,
    sectors: list[SectorBlock],
) -> tuple[int, str, str, float]:
    hints = flow_match_hints(row.backend_flow)
    candidates = _candidate_sectors(sectors, row.sector_label, row.backend_flow)

    best_sector: SectorBlock | None = None
    best_title = ""
    best_score = 0.0

    for sector in candidates:
        for image in sector.images:
            title = (image.title_hint or "").strip()
            if not title or image.skipped or should_skip_flow_title(title):
                continue
            score = _score_guide_title(hints, title)
            if score > best_score:
                best_score = score
                best_sector = sector
                best_title = title

    override_name = BACKEND_FLOW_GUIDE_OVERRIDES.get(row.backend_flow.strip())
    if override_name:
        sector_index, sector_title, guide_title = _resolve_guide_title_by_name(
            override_name,
            sectors,
            row.sector_label,
            row.backend_flow,
        )
        if guide_title:
            return sector_index, sector_title, guide_title, best_score

    if best_sector is None or best_score < MATCH_THRESHOLD:
        return 0, "", "", best_score
    return best_sector.index, best_sector.title, best_title, best_score


def make_target_key(sector_index: int, guide_title: str) -> str:
    return f"s{sector_index}:{display_form_name(guide_title)}"


def make_row_target_key(row: TemplateTableRow) -> str:
    flow = display_form_name(row.backend_flow)
    return f"{row.sheet_name}:{row.row_index}:{flow}"


def resolve_row_oa_forms(
    original_form_name: str,
    backend_flow: str,
    *,
    settings: Settings | None = None,
) -> tuple[list[str], bool, float, str]:
    """匹配 B 列表单名到 OA 表单；无匹配则视为「新增」。

    返回 (form_slugs, is_new_form, best_score, reason)。
    """
    settings = settings or get_settings()
    raw = (original_form_name or "").strip()
    parts = _split_form_cell(raw) if raw else []
    parts = [p for p in parts if p and p != "新增"]

    if not parts or raw == "新增":
        return [], True, 0.0, "新增"

    slugs: list[str] = []
    best_score = 0.0
    for part in parts:
        slug, _ = resolve_template_form_slug(part, backend_flow, settings=settings)
        if slug and slug not in slugs:
            slugs.append(slug)

    if slugs:
        return slugs, False, best_score, ""

    query = parts[0]
    hits = search_forms(query, limit=5, settings=settings) if query else []
    if hits:
        best_score = float(hits[0].get("match_score") or 0)
    return [], True, best_score, "新增（未匹配 OA 表单）"


def load_template_form_record(
    form_slug: str,
    *,
    settings: Settings | None = None,
):
    """加载模板目标 OA 表单，支持 FORM_LOAD_ALIASES。"""
    from schemas.form import RawFormRecord

    settings = settings or get_settings()
    slug = (form_slug or "").strip()
    for candidate in (slug, FORM_LOAD_ALIASES.get(slug, "")):
        if not candidate:
            continue
        record = load_form(candidate, settings)
        if record and record.success:
            if slug and slug != record.template_name:
                return RawFormRecord(
                    **{
                        **record.model_dump(),
                        "template_name": slug,
                    }
                )
            return record
    return None


def resolve_template_form_slug(
    original_form_name: str,
    backend_flow: str,
    *,
    settings: Settings | None = None,
) -> tuple[str | None, str]:
    settings = settings or get_settings()
    candidates = [n.strip() for n in (original_form_name or "").splitlines() if n.strip()]
    if backend_flow.strip():
        candidates.append(backend_flow.strip())

    for name in candidates:
        if load_template_form_record(name, settings=settings):
            return name, ""

    query = candidates[0] if candidates else backend_flow
    if query:
        hits = search_forms(query, limit=5, settings=settings)
        if hits:
            best = hits[0]
            score = float(best.get("match_score") or 0)
            if score >= FORM_MATCH_THRESHOLD:
                slug = str(best.get("template_name") or "")
                record = load_form(slug, settings)
                if record and record.success:
                    return slug, ""

    return None, "未找到已成功提取的 OA 表单"


def _flow_business_suffix(name: str) -> str:
    for suffix in ("报销单", "支付单", "付款单", "借款单"):
        if suffix in (name or ""):
            return suffix
    return ""


def _resolve_target_form(
    target: TemplateGenerationTarget,
    *,
    settings: Settings,
) -> None:
    """多行映射同一指南流程时，按指南流程类型选取对应 OA 表单（如报销单不用借款单表单）。"""
    if len(target.source_rows) <= 1:
        return

    guide_norm = display_form_name(target.guide_title)
    preferred_backend = GUIDE_FLOW_PREFERRED_BACKEND.get(guide_norm)
    preferred_form = GUIDE_FLOW_PREFERRED_FORM.get(guide_norm)

    if preferred_form:
        if load_template_form_record(preferred_form, settings=settings):
            target.form_slug = preferred_form
            target.enabled = True
            target.skip_reason = ""
            return

    best_row: TemplateTableRow | None = None
    best_score = -1.0
    guide_suffix = _flow_business_suffix(guide_norm)
    for row in target.source_rows:
        if preferred_backend and row.backend_flow.strip() != preferred_backend:
            continue
        row_suffix = _flow_business_suffix(row.backend_flow)
        if guide_suffix and row_suffix and guide_suffix != row_suffix:
            continue
        score = string_similarity(row.backend_flow, guide_norm)
        if guide_suffix and guide_suffix == row_suffix:
            score += 0.05
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None and preferred_backend:
        for row in target.source_rows:
            if row.backend_flow.strip() == preferred_backend:
                best_row = row
                break

    if best_row is None:
        return

    form_slug, reason = resolve_template_form_slug(
        best_row.original_form_name,
        best_row.backend_flow,
        settings=settings,
    )
    if form_slug:
        target.form_slug = form_slug
        target.enabled = True
        target.skip_reason = ""
    else:
        target.enabled = False
        target.skip_reason = reason


def _resolve_merged_target_forms(
    target: TemplateGenerationTarget,
    *,
    settings: Settings,
) -> bool:
    """指南流程需融合多个 OA 表单字段时，收集各 C 列对应表单 slug。"""
    guide_norm = display_form_name(target.guide_title)
    backend_keys = GUIDE_FLOW_MERGED_FORMS.get(guide_norm)
    if not backend_keys:
        return False

    slugs: list[str] = []
    for backend in backend_keys:
        for row in target.source_rows:
            if row.backend_flow.strip() != backend:
                continue
            slug, _ = resolve_template_form_slug(
                row.original_form_name,
                row.backend_flow,
                settings=settings,
            )
            if slug and slug not in slugs:
                slugs.append(slug)

    if not slugs:
        target.enabled = False
        target.skip_reason = "未找到借款/还款 OA 表单"
        return True

    target.form_slugs = slugs
    target.form_slug = " + ".join(slugs)
    target.enabled = True
    target.skip_reason = ""
    if len(slugs) < len(backend_keys):
        target.skip_reason = f"仅找到 {len(slugs)}/{len(backend_keys)} 个表单，将合并已有表单字段"
    return True


def build_template_match_report(
    *,
    settings: Settings | None = None,
    parse_result: GuideParseResult | None = None,
    template_rows: list[TemplateTableRow] | None = None,
) -> TemplateMatchReport:
    """任务表待写入 C 列 → OA 表单匹配（一行一流程，无指南去重）。"""
    settings = settings or get_settings()
    rows = (
        template_rows
        if template_rows is not None
        else load_pending_write_template_rows(settings.flow_task_table_path)
    )
    result = parse_result if parse_result is not None else load_result(settings)
    sectors = list(result.sectors) if result else []

    row_matches: list[TemplateRowMatch] = []
    targets: list[TemplateGenerationTarget] = []

    for row in rows:
        flow_name = correct_flow_title(row.backend_flow.strip())
        form_slugs, is_new, form_score, form_reason = resolve_row_oa_forms(
            row.original_form_name,
            row.backend_flow,
            settings=settings,
        )
        sector_index, sector_title, flowchart_title, _flow_score = _match_row_to_guide(row, sectors)
        target_key = make_row_target_key(row)
        oa_display = " + ".join(form_slugs) if form_slugs else "新增"

        item = TemplateRowMatch(
            row=row,
            oa_form_slugs=form_slugs,
            oa_forms_display=oa_display,
            is_new_form=is_new,
            form_match_score=round(form_score, 3),
            guide_sector_index=sector_index,
            guide_sector_title=sector_title,
            flowchart_title=flowchart_title or flow_name,
            target_key=target_key,
            skip_reason=form_reason if is_new else "",
        )
        row_matches.append(item)

        target = TemplateGenerationTarget(
            target_key=target_key,
            guide_sector_index=sector_index,
            guide_sector_title=sector_title or row.sector_label,
            guide_title=flow_name,
            workflow_key=display_form_name(flow_name),
            form_slug=oa_display,
            form_slugs=form_slugs,
            is_new_form=is_new,
            app_display_name=ai_app_display_name(flow_name),
            enabled=True,
            source_rows=[row],
            backend_flow_labels=[flow_name],
            skip_reason="" if (form_slugs or is_new) else form_reason,
        )
        targets.append(target)

    targets.sort(key=lambda t: (t.guide_sector_index, t.guide_title, t.target_key))
    return TemplateMatchReport(rows=row_matches, targets=targets)


def resolve_vlm_lookup_titles(task_flow_title: str) -> list[str]:
    """任务表流程名 → 办事指南 VLM 标题候选（含人工映射）。"""
    text = (task_flow_title or "").strip()
    titles: list[str] = []
    override = VLM_FLOW_TITLE_OVERRIDES.get(text)
    if not override:
        norm = display_form_name(text)
        for key, value in VLM_FLOW_TITLE_OVERRIDES.items():
            if display_form_name(key) == norm:
                override = value
                break
    if override:
        titles.extend([override, display_form_name(override)])
        titles.extend(flow_match_hints(override))
    titles.extend([text, display_form_name(text)])
    titles.extend(flow_match_hints(text))
    return list(dict.fromkeys(t for t in titles if t))


def find_parsed_flow_by_guide(
    guide_title: str,
    *,
    sector_index: int | None = None,
    settings: Settings | None = None,
    parse_result: GuideParseResult | None = None,
    _allow_any_sector: bool = True,
) -> WorkflowFlowResult | None:
    settings = settings or get_settings()
    result = parse_result if parse_result is not None else load_result(settings)
    if not result:
        return None

    lookup_titles = resolve_vlm_lookup_titles(guide_title)
    exact_norms = {display_form_name(t) for t in lookup_titles}

    def _scan(*, restrict_sector: int | None) -> WorkflowFlowResult | None:
        best: WorkflowFlowResult | None = None
        best_score = 0.0
        best_nodes = -1
        for item in result.sector_results or []:
            sector = item.sector
            if restrict_sector is not None and sector.index != restrict_sector:
                continue
            for flow in item.flows:
                if flow.skipped or not flow.success or not flow.nodes:
                    continue
                for candidate in (flow.image.title_hint, flow.title):
                    cand_norm = display_form_name(candidate)
                    if cand_norm in exact_norms:
                        return flow
                for hint in lookup_titles:
                    for candidate in (flow.image.title_hint, flow.title):
                        score = string_similarity(hint, candidate)
                        if score > best_score or (score == best_score and len(flow.nodes) > best_nodes):
                            best_score = score
                            best_nodes = len(flow.nodes)
                            best = flow
        if best_score >= MATCH_THRESHOLD and best is not None:
            return best
        return None

    found = _scan(restrict_sector=sector_index)
    if found is not None:
        return found
    if sector_index is not None and _allow_any_sector:
        return find_parsed_flow_by_guide(
            guide_title,
            sector_index=None,
            settings=settings,
            parse_result=result,
            _allow_any_sector=False,
        )
    return None
