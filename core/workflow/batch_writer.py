"""工作流批量 VLM 解析与神笔平台批量写入。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.config.settings import Settings, get_settings
from core.workflow.shenbi_builder import (
    build_template_workflow_payloads,
    build_workflow_payloads,
    save_generated_payloads,
)
from core.workflow.shenbi_client import (
    ShenbiApiError,
    _sync_settings_tenant,
    apply_registry_to_payloads,
    load_workflow_registry,
    registry_app_verified,
    save_workflow_models,
    session_cookie_configured,
    write_result_tree_confirmed,
)
from core.workflow.template_matcher import (
    TemplateGenerationTarget,
    TemplateMatchReport,
    build_template_match_report,
    template_registry_name,
)
from core.workflow.workflow_catalog import (
    discover_catalog_entries,
    list_writable_workflow_keys,
    save_catalog_index,
)
from schemas.workflow import GuideParseResult

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class BatchWriteItem:
    workflow_key: str
    success: bool
    error: str = ""
    app_id: str = ""
    proc_id: str = ""
    tree_confirmed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchWriteReport:
    total: int
    succeeded: int
    failed: int
    items: list[BatchWriteItem] = field(default_factory=list)

    @property
    def errors(self) -> list[BatchWriteItem]:
        return [item for item in self.items if not item.success]


def run_batch_vlm_parse(
    *,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
) -> GuideParseResult:
    """批量模式：先完成 VLM 解析并写入 parse 缓存（复用已有成功结果）。"""
    from core.workflow.pipeline import run_guide_pipeline

    settings = settings or get_settings()
    batch_settings = settings.model_copy(update={"guide_parse_use_cache": True})
    result = run_guide_pipeline(
        settings=batch_settings,
        vlm_flow_limit=0,
        per_sector_flow_limit=batch_settings.workflow_per_sector_limit,
        on_progress=on_progress,
    )
    entries = discover_catalog_entries(settings=batch_settings, parse_result=result)
    save_catalog_index(entries, settings=batch_settings)
    return result


def run_batch_write(
    workflow_keys: list[str] | None = None,
    *,
    settings: Settings | None = None,
    token: str,
    on_progress: ProgressCallback | None = None,
) -> BatchWriteReport:
    """批量写入：依赖 parse 缓存，逐条生成 JSON 并写入神笔平台。"""
    settings = settings or get_settings()
    batch_settings = settings.model_copy(update={"guide_parse_use_cache": True})
    keys = workflow_keys or list_writable_workflow_keys(settings=batch_settings)
    items: list[BatchWriteItem] = []
    total = len(keys)

    for idx, workflow_key in enumerate(keys, start=1):
        if on_progress:
            on_progress(idx - 1, total, f"正在写入 {workflow_key}…")
        try:
            payloads = build_workflow_payloads(workflow_key, settings=batch_settings)
            payloads = apply_registry_to_payloads(payloads, workflow_key, settings=batch_settings)
            save_generated_payloads(payloads, settings=batch_settings)
            write_result = save_workflow_models(
                payloads,
                workflow_name=workflow_key,
                settings=batch_settings,
                token=token,
            )
            model = write_result.get("model") if isinstance(write_result.get("model"), dict) else {}
            tree_ok = write_result_tree_confirmed(write_result, local_model=model)
            if not tree_ok and isinstance(model.get("wfSimpleTaskInfo"), dict):
                raise ShenbiApiError(
                    "平台未回传完整环节树（批量写入不会以空壳覆盖设计器流程，请单独重试或检查 token）"
                )
            items.append(
                BatchWriteItem(
                    workflow_key=workflow_key,
                    success=True,
                    app_id=str(write_result.get("app_id") or ""),
                    proc_id=str(write_result.get("proc_id") or ""),
                    tree_confirmed=tree_ok,
                    detail={"results": write_result.get("results") or []},
                )
            )
        except Exception as exc:
            items.append(
                BatchWriteItem(
                    workflow_key=workflow_key,
                    success=False,
                    error=str(exc),
                )
            )
        if on_progress:
            on_progress(idx, total, workflow_key)

    succeeded = sum(1 for item in items if item.success)
    return BatchWriteReport(
        total=total,
        succeeded=succeeded,
        failed=total - succeeded,
        items=items,
    )


def run_single_write(
    workflow_key: str,
    *,
    settings: Settings | None = None,
    token: str,
) -> dict[str, Any]:
    """单独写入：从 Word 直接 VLM 该流程并更新缓存，再写入神笔平台。"""
    settings = settings or get_settings()
    single_settings = settings.model_copy(update={"guide_parse_use_cache": False})
    payloads = build_workflow_payloads(workflow_key, settings=single_settings)
    payloads = apply_registry_to_payloads(payloads, workflow_key, settings=single_settings)
    out_path = save_generated_payloads(payloads, settings=single_settings)
    write_result = save_workflow_models(
        payloads,
        workflow_name=workflow_key,
        settings=single_settings,
        token=token,
    )
    entries = discover_catalog_entries(settings=single_settings)
    save_catalog_index(entries, settings=single_settings)
    return {
        "payloads": payloads,
        "out_path": str(out_path),
        "write_result": write_result,
    }


def run_template_batch_vlm_parse(
    targets: list[TemplateGenerationTarget] | None = None,
    *,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
) -> GuideParseResult:
    """模板批量模式：全量 VLM 解析（无板块数量限制）。"""
    from core.workflow.pipeline import run_guide_pipeline

    settings = settings or get_settings()
    report = build_template_match_report(settings=settings)
    needed = targets or report.enabled_targets
    batch_settings = settings.model_copy(
        update={"guide_parse_use_cache": True, "workflow_per_sector_limit": 0}
    )
    result = run_guide_pipeline(
        settings=batch_settings,
        vlm_flow_limit=0,
        per_sector_flow_limit=0,
        on_progress=on_progress,
    )
    return result


def run_template_batch_write(
    targets: list[TemplateGenerationTarget] | None = None,
    *,
    settings: Settings | None = None,
    token: str,
    on_progress: ProgressCallback | None = None,
) -> BatchWriteReport:
    """模板表匹配目标批量写入（AI_ 前缀，按办事指南去重）。"""
    from core.config.settings import reload_settings

    settings = _sync_settings_tenant(settings or reload_settings())
    batch_settings = settings.model_copy(update={"guide_parse_use_cache": True})
    report = build_template_match_report(settings=batch_settings)
    items_to_write = targets or report.enabled_targets
    items: list[BatchWriteItem] = []
    total = len(items_to_write)

    for idx, target in enumerate(items_to_write, start=1):
        label = target.app_display_name or target.guide_title
        if on_progress:
            on_progress(idx - 1, total, f"正在写入 {label}…")
        registry_key = template_registry_name(target.target_key)
        try:
            payloads = build_template_workflow_payloads(target, settings=batch_settings)
            registry = load_workflow_registry(registry_key, settings=batch_settings)
            if registry_app_verified(registry):
                payloads = apply_registry_to_payloads(payloads, registry_key, settings=batch_settings)
            save_generated_payloads(payloads, settings=batch_settings)
            write_result = save_workflow_models(
                payloads,
                workflow_name=registry_key,
                settings=batch_settings,
                token=token,
                template_mode=True,
                app_mc=target.app_display_name,
                form_slug=target.form_slug,
                sector_title=target.guide_sector_title,
                sector_index=target.guide_sector_index,
            )
            model = write_result.get("model") if isinstance(write_result.get("model"), dict) else {}
            tree_ok = write_result_tree_confirmed(write_result, local_model=model)
            runtime_ready = bool(write_result.get("runtime_ready"))
            if not runtime_ready and session_cookie_configured(batch_settings):
                raise ShenbiApiError(
                    str(
                        (write_result.get("runtime_check") or {}).get("message")
                        or "流程未发布到运行时"
                    )
                )
            if not tree_ok and isinstance(model.get("wfSimpleTaskInfo"), dict):
                raise ShenbiApiError(
                    "平台未回传完整环节树（批量写入不会以空壳覆盖设计器流程，请单独重试或检查 token）"
                )
            items.append(
                BatchWriteItem(
                    workflow_key=registry_key,
                    success=True,
                    app_id=str(write_result.get("app_id") or ""),
                    proc_id=str(write_result.get("proc_id") or ""),
                    tree_confirmed=tree_ok,
                    detail={
                        "results": write_result.get("results") or [],
                        "runtime_ready": runtime_ready,
                        "guide_title": target.guide_title,
                        "target_key": target.target_key,
                        "backend_flows": target.backend_flow_labels,
                    },
                )
            )
        except Exception as exc:
            items.append(
                BatchWriteItem(
                    workflow_key=registry_key,
                    success=False,
                    error=str(exc),
                    detail={"guide_title": target.guide_title, "target_key": target.target_key},
                )
            )
        if on_progress:
            on_progress(idx, total, label)

    succeeded = sum(1 for item in items if item.success)
    return BatchWriteReport(
        total=total,
        succeeded=succeeded,
        failed=total - succeeded,
        items=items,
    )


def run_template_batch_publish(
    targets: list[TemplateGenerationTarget] | None = None,
    *,
    settings: Settings | None = None,
    token: str,
    on_progress: ProgressCallback | None = None,
) -> BatchWriteReport:
    """对已写入 registry 的流程批量发布到运行时（不重复 model-save）。"""
    from core.config.settings import reload_settings
    from core.workflow.shenbi_client import republish_workflow_from_registry

    settings = _sync_settings_tenant(settings or reload_settings())
    report = build_template_match_report(settings=settings)
    items_to_publish = targets or report.enabled_targets
    items: list[BatchWriteItem] = []
    total = len(items_to_publish)

    for idx, target in enumerate(items_to_publish, start=1):
        label = target.app_display_name or target.guide_title
        registry_key = template_registry_name(target.target_key)
        if on_progress:
            on_progress(idx - 1, total, f"正在发布 {label}…")
        try:
            result = republish_workflow_from_registry(registry_key, settings=settings, token=token)
            items.append(
                BatchWriteItem(
                    workflow_key=registry_key,
                    success=True,
                    app_id=str(result.get("app_id") or ""),
                    proc_id=str(result.get("proc_id") or ""),
                    detail={
                        "guide_title": target.guide_title,
                        "target_key": target.target_key,
                        "publish": result.get("publish"),
                        "runtime_check": result.get("runtime_check"),
                    },
                )
            )
        except Exception as exc:
            items.append(
                BatchWriteItem(
                    workflow_key=registry_key,
                    success=False,
                    error=str(exc),
                    detail={"guide_title": target.guide_title, "target_key": target.target_key},
                )
            )
        if on_progress:
            on_progress(idx, total, label)

    succeeded = sum(1 for item in items if item.success)
    return BatchWriteReport(
        total=total,
        succeeded=succeeded,
        failed=total - succeeded,
        items=items,
    )


def run_template_single_write(
    target: TemplateGenerationTarget,
    *,
    settings: Settings | None = None,
    token: str,
) -> dict[str, Any]:
    """模板表单独写入：按需 VLM 后写入神笔（AI_ 应用名）。"""
    settings = settings or get_settings()
    single_settings = settings.model_copy(update={"guide_parse_use_cache": False})
    registry_key = template_registry_name(target.target_key)
    payloads = build_template_workflow_payloads(target, settings=single_settings)
    registry = load_workflow_registry(registry_key, settings=single_settings)
    if registry_app_verified(registry):
        payloads = apply_registry_to_payloads(payloads, registry_key, settings=single_settings)
    out_path = save_generated_payloads(payloads, settings=single_settings)
    write_result = save_workflow_models(
        payloads,
        workflow_name=registry_key,
        settings=single_settings,
        token=token,
        template_mode=True,
        app_mc=target.app_display_name,
        form_slug=target.form_slug,
        sector_title=target.guide_sector_title,
        sector_index=target.guide_sector_index,
    )
    return {
        "payloads": payloads,
        "out_path": str(out_path),
        "write_result": write_result,
        "target": target,
    }


def load_template_report(*, settings: Settings | None = None) -> TemplateMatchReport:
    settings = settings or get_settings()
    return build_template_match_report(settings=settings)
