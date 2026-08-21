"""流程写入神笔平台。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config.settings import env_file_mtime, reload_settings, switch_shenbi_environment
from core.config.shenbi_environments import SHENBI_ENVIRONMENT_OPTIONS, get_shenbi_config
from core.form.name_utils import workflow_name_matches_query
from core.form.task_table import load_pending_write_rows
from core.workflow.batch_write_results import (
    apply_batch_report_to_table,
    ensure_write_results_table,
    export_write_results_to_excel,
    save_write_results,
    select_targets_for_rewrite,
    table_to_dataframe_rows,
    dataframe_rows_to_table,
    write_results_excel_bytes,
)
from core.workflow.batch_writer import (
    load_template_report,
    run_template_batch_publish,
    run_template_batch_vlm_parse,
    run_template_batch_write,
    run_template_single_write,
)
from core.workflow.result_store import get_store_meta, is_parse_run_complete, load_result
from core.workflow.shenbi_builder import (
    build_template_workflow_payloads,
    save_generated_payloads,
)
from core.workflow.shenbi_client import (
    ShenbiApiError,
    apply_registry_to_payloads,
    check_shenbi_auth,
    load_workflow_registry,
    prepare_save_payload,
    registry_app_verified,
)
from core.workflow.template_matcher import (
    TemplateGenerationTarget,
    build_template_match_report,
    template_registry_name,
)


def _target_label(target: TemplateGenerationTarget) -> str:
    form_part = target.form_slug if not target.is_new_form else "新增"
    return f"{target.app_display_name}（{target.guide_title} · {form_part}）"


def _target_search_blob(target: TemplateGenerationTarget) -> str:
    parts = [
        target.app_display_name,
        target.guide_title,
        target.guide_sector_title,
        target.form_slug,
        target.workflow_key,
        " ".join(target.backend_flow_labels),
        " ".join(target.form_slugs),
    ]
    return " ".join(p for p in parts if p)


def _filter_targets(
    targets: list[TemplateGenerationTarget],
    query: str,
) -> list[TemplateGenerationTarget]:
    q = (query or "").strip()
    if not q:
        return list(targets)
    return [t for t in targets if workflow_name_matches_query(q, _target_search_blob(t))]


def _target_list_row(target: TemplateGenerationTarget) -> dict:
    return {
        "应用名": target.app_display_name,
        "流程(C列)": target.guide_title,
        "板块": target.guide_sector_title or "—",
        "OA 表单": "新增" if target.is_new_form else target.form_slug,
        "表单明细": " + ".join(target.form_slugs) if target.form_slugs else ("新增" if target.is_new_form else ""),
        "可写入": "是" if target.enabled else "否",
        "备注": target.skip_reason,
    }


def _sync_token_from_env() -> None:
    """检测 .env 变更（含 Cursor 直接改 token），同步侧边栏。"""
    mtime = env_file_mtime()
    if mtime is None:
        return
    if st.session_state.get("_env_mtime") == mtime:
        return

    fresh = reload_settings()
    st.session_state["_env_mtime"] = mtime
    st.session_state["fighter_auth_token"] = get_shenbi_config(fresh).api_token


_sync_token_from_env()
settings = reload_settings()
shenbi_cfg = get_shenbi_config(settings)

st.set_page_config(page_title="流程写入", layout="wide")
st.title("流程写入 · 神笔平台")
st.caption(
    f"当前环境：**{shenbi_cfg.label}**（{shenbi_cfg.base_url}） · "
    "基于流程任务表待写入清单（C 列）匹配野马集团 OA 表单；"
    "未匹配到表单标注「新增」，仅含流程图与最小表单字段。"
    " 应用名前缀 AI_，一行一流程。"
)

task_table_path = settings.flow_task_table_path
if not task_table_path.is_file():
    st.error(f"未找到流程任务表：{task_table_path}")
    st.stop()

try:
    if st.session_state.get("_template_report_refresh"):
        template_report = build_template_match_report(settings=settings)
        st.session_state.pop("_template_report_refresh", None)
    else:
        template_report = load_template_report(settings=settings)
except Exception as exc:
    st.error(f"读取流程任务表失败：{exc}")
    st.stop()

enabled_targets = template_report.enabled_targets
all_targets = template_report.targets
pending_rows = load_pending_write_rows(task_table_path)
pending_by_key = {(r.sheet_name, r.row_index): r for r in pending_rows}
write_results_table = ensure_write_results_table(enabled_targets, settings=settings)

with st.sidebar:
    st.subheader("神笔工作环境")
    env_keys = list(SHENBI_ENVIRONMENT_OPTIONS.keys())
    current_env = (settings.shenbi_environment or "ym").strip().lower()
    if current_env not in env_keys:
        current_env = "ym"
    selected_env = st.selectbox(
        "工作环境",
        options=env_keys,
        index=env_keys.index(current_env),
        format_func=lambda k: SHENBI_ENVIRONMENT_OPTIONS[k],
        help="开发环境=cloudschool；野马数智化平台=ym.zhiduo.net。也可在对话中说「切换到开发环境」。",
    )
    if selected_env != current_env:
        if st.button("确认切换环境", use_container_width=True, type="primary"):
            switch_shenbi_environment(selected_env)
            st.session_state["fighter_auth_token"] = get_shenbi_config(reload_settings()).api_token
            st.rerun()

    st.subheader("神笔平台连接")
    token = st.text_input(
        "fighter-auth-token",
        value=shenbi_cfg.api_token,
        key="fighter_auth_token",
        type="password",
        help="默认读 .env；Cursor 更新 SHENBI_API_TOKEN 后，下次点击页面任意按钮即自动同步",
    )
    st.text_input("API 地址", value=shenbi_cfg.base_url, disabled=True)
    st.caption(f"写入角色：{shenbi_cfg.admin_role_name}（{shenbi_cfg.admin_role_code}）")
    ssl_verify = st.toggle(
        "校验 HTTPS 证书",
        value=settings.shenbi_ssl_verify,
        help="神笔平台证书过期时可关闭（仅临时调试）",
    )
    session_cookie = st.text_input(
        "SESSION Cookie",
        value=settings.shenbi_session_cookie,
        type="password",
        help="必填（不能为 SESSION=SESSION 占位值）：从浏览器 DevTools 复制完整 Cookie，与 token 同一会话。"
        " 否则工作台会提示「查无该流程定义信息」。",
    )
    st.caption("写入：tBaidaApp/saveOrUpdate → model-save → 发布")
    if st.button("检测神笔连接", use_container_width=True):
        diag = check_shenbi_auth(
            settings=settings.model_copy(update={"shenbi_ssl_verify": ssl_verify}),
            token=token,
        )
        st.session_state.shenbi_diag = diag
    diag = st.session_state.get("shenbi_diag")
    if diag:
        if diag.get("auth_ok"):
            st.success("Token 有效")
        else:
            st.error(diag.get("auth_message") or "Token 无效")

    st.divider()
    store_meta = get_store_meta(settings)
    if store_meta.get("valid"):
        parsed = store_meta.get("parsed_flowcharts") or 0
        total = store_meta.get("total_flowcharts") or 0
        stale = store_meta.get("stale")
        if stale:
            st.warning("解析缓存已过期，请重新批量解析")
        else:
            st.caption(f"解析缓存：{parsed}/{total} 张流程图")
    else:
        st.caption("尚无解析缓存")

    st.divider()
    st.caption(f"流程任务表：{task_table_path.name}")
    st.metric("待写入 C 列", len(pending_rows))
    st.metric("OA 已匹配", template_report.matched_count)
    st.metric("新增流程", template_report.new_form_count)
    st.metric("可写入", len(enabled_targets))

tab_single, tab_batch, tab_match = st.tabs(["单独写入", "批量写入", "匹配结果"])

col_refresh, _ = st.columns([1, 5])
with col_refresh:
    if st.button("刷新匹配结果", key="refresh_template_report"):
        st.session_state["_template_report_refresh"] = True
        st.rerun()

with tab_single:
    if not enabled_targets:
        st.warning("暂无可写入流程，请先在「匹配结果」查看未匹配或未提取 OA 表单的条目。")
    else:
        search_query = st.text_input(
            "搜索目标流程（应用名 / 指南流程 / OA 表单 / C 列）",
            placeholder="例如：费用报销、资金调拨、AI_文旅",
            key="single_target_search",
        )
        filtered_targets = _filter_targets(enabled_targets, search_query)
        if search_query.strip() and not filtered_targets:
            st.warning(f"未找到匹配「{search_query.strip()}」的可写入流程")
        else:
            st.caption(
                f"可写入 {len(enabled_targets)} 个"
                + (f" · 当前匹配 {len(filtered_targets)} 个" if search_query.strip() else "")
            )
            labels = [_target_label(t) for t in filtered_targets]
            selected_idx = st.selectbox(
                "选择目标流程（一行一流程）",
                options=range(len(filtered_targets)),
                format_func=lambda i: labels[i],
                index=0,
                key="single_template_target",
            )
            target = filtered_targets[selected_idx]

            registry_key = template_registry_name(target.target_key)
            _registry = load_workflow_registry(registry_key, settings=settings)

            col_gen, col_write = st.columns([1, 1])
            with col_gen:
                generate_clicked = st.button("生成接口 JSON", type="primary", key="single_gen")
            with col_write:
                write_clicked = st.button("一键写入神笔平台", type="secondary", key="single_write")

            if generate_clicked:
                try:
                    single_settings = settings.model_copy(update={"guide_parse_use_cache": False})
                    with st.spinner(f"正在解析「{target.guide_title}」并生成 JSON…"):
                        payloads = build_template_workflow_payloads(target, settings=single_settings)
                        if _registry and registry_app_verified(_registry):
                            payloads = apply_registry_to_payloads(
                                payloads, registry_key, settings=single_settings
                            )
                        out_path = save_generated_payloads(payloads, settings=single_settings)
                    st.session_state.shenbi_payloads = payloads
                    st.session_state.shenbi_out_path = str(out_path)
                    st.success("JSON 已生成")
                except Exception as exc:
                    st.error(f"生成失败：{exc}")

            if write_clicked:
                _sync_token_from_env()
                fresh_settings = reload_settings()
                token_val = (st.session_state.get("fighter_auth_token") or fresh_settings.shenbi_api_token or "").strip()
                if not token_val:
                    st.error("请填写 fighter-auth-token")
                else:
                    try:
                        api_settings = fresh_settings.model_copy(
                            update={"shenbi_ssl_verify": ssl_verify, "shenbi_session_cookie": session_cookie}
                        )
                        with st.spinner(f"正在写入「{target.app_display_name}」…"):
                            outcome = run_template_single_write(target, settings=api_settings, token=token_val)
                        payloads = outcome["payloads"]
                        write_result = outcome["write_result"]
                        st.session_state.shenbi_payloads = {**payloads, "model": write_result["model"]}
                        st.session_state.shenbi_out_path = outcome["out_path"]
                        st.success("已成功写入神笔平台")
                        if write_result.get("app_mc"):
                            st.info(
                                f"应用名称：**{write_result['app_mc']}** · appId `{write_result.get('app_id', '')}` · "
                                f"请在平台「我的应用」按完整名称（含 **AI_** 前缀）搜索，板块选 **野马集团**。"
                            )
                    except ShenbiApiError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"写入异常：{exc}")

with tab_batch:
    st.caption(
        f"批量模式：先全量 VLM 解析办事指南（无板块数量限制），再按模板表匹配结果逐条写入。"
        f" 共 {len(enabled_targets)} 个流程（任务表 {len(pending_rows)} 行 C 列，一行一流程）。"
    )

    parse_result = load_result(settings)
    parse_complete = parse_result is not None and is_parse_run_complete(parse_result)
    col_info, col_parse, col_write_all, col_publish_all = st.columns([2, 1, 1, 1])

    with col_info:
        st.metric("可写入流程", len(enabled_targets))
        skipped = len(all_targets) - len(enabled_targets)
        if skipped:
            st.caption(f"另有 {skipped} 个流程暂不可写入")
        new_count = sum(1 for t in enabled_targets if t.is_new_form)
        if new_count:
            st.caption(f"其中 {new_count} 个为「新增」流程（无 OA 表单，仅流程图 + 最小字段）")

    with col_parse:
        batch_parse_clicked = st.button("批量 VLM 解析", type="primary", key="batch_parse")
    with col_write_all:
        batch_write_clicked = st.button("一键批量写入", type="secondary", key="batch_write")
    with col_publish_all:
        batch_publish_clicked = st.button("批量发布流程", type="secondary", key="batch_publish")

    if batch_parse_clicked:
        progress = st.progress(0.0, text="准备批量解析…")
        status = st.empty()

        def _on_parse_progress(current: int, total: int, label: str) -> None:
            ratio = current / total if total else 0.0
            progress.progress(min(ratio, 1.0), text=f"({current}/{total}) {label}")

        try:
            batch_settings = settings.model_copy(
                update={"guide_parse_use_cache": True, "shenbi_ssl_verify": ssl_verify, "workflow_per_sector_limit": 0}
            )
            with st.spinner("正在全量 VLM 解析办事指南…"):
                result = run_template_batch_vlm_parse(settings=batch_settings, on_progress=_on_parse_progress)
            progress.progress(1.0, text="解析完成")
            st.success(f"批量解析完成：已识别 {result.parsed_flowcharts} 张流程图")
            if result.errors:
                with st.expander("解析警告", expanded=False):
                    for err in result.errors:
                        st.markdown(f"- {err}")
            st.rerun()
        except Exception as exc:
            st.error(f"批量解析失败：{exc}")

    if batch_publish_clicked:
        _sync_token_from_env()
        fresh_settings = reload_settings()
        token_val = (st.session_state.get("fighter_auth_token") or fresh_settings.shenbi_api_token or "").strip()
        if not token_val:
            st.error("请填写 fighter-auth-token")
        else:
            progress = st.progress(0.0, text="准备批量发布…")

            def _on_publish_progress(current: int, total: int, label: str) -> None:
                ratio = current / total if total else 0.0
                progress.progress(min(ratio, 1.0), text=f"({current}/{total}) {label}")

            try:
                api_settings = fresh_settings.model_copy(
                    update={
                        "shenbi_ssl_verify": ssl_verify,
                        "shenbi_session_cookie": session_cookie,
                    }
                )
                with st.spinner(f"正在批量发布 {len(enabled_targets)} 个流程到运行时…"):
                    publish_report = run_template_batch_publish(
                        enabled_targets,
                        settings=api_settings,
                        token=token_val,
                        on_progress=_on_publish_progress,
                    )
                progress.progress(1.0, text="发布完成")
                if publish_report.failed:
                    st.warning(
                        f"发布完成 {publish_report.succeeded}/{publish_report.total}，"
                        f"{publish_report.failed} 个失败（请确认 SESSION Cookie 与 token 同一会话）"
                    )
                else:
                    st.success(f"全部 {publish_report.total} 个流程已发布到运行时")
            except Exception as exc:
                st.error(f"批量发布异常：{exc}")

    if batch_write_clicked:
        _sync_token_from_env()
        fresh_settings = reload_settings()
        token_val = (st.session_state.get("fighter_auth_token") or fresh_settings.shenbi_api_token or "").strip()
        if not token_val:
            st.error("请填写 fighter-auth-token")
        elif not parse_complete:
            st.warning("请先完成「批量 VLM 解析」，或确认解析缓存完整后再写入。")
        else:
            progress = st.progress(0.0, text="准备批量写入…")

            def _on_write_progress(current: int, total: int, label: str) -> None:
                ratio = current / total if total else 0.0
                progress.progress(min(ratio, 1.0), text=f"({current}/{total}) {label}")

            try:
                api_settings = fresh_settings.model_copy(
                    update={
                        "guide_parse_use_cache": True,
                        "shenbi_ssl_verify": ssl_verify,
                        "shenbi_session_cookie": session_cookie,
                    }
                )
                with st.spinner(f"正在批量写入 {len(enabled_targets)} 个流程…"):
                    report = run_template_batch_write(
                        enabled_targets,
                        settings=api_settings,
                        token=token_val,
                        on_progress=_on_write_progress,
                    )
                progress.progress(1.0, text="写入完成")
                write_results_table = apply_batch_report_to_table(write_results_table, report)
                save_write_results(write_results_table, settings=settings)
                st.session_state.batch_write_report = report
                if report.failed:
                    st.warning(f"完成 {report.succeeded}/{report.total}，{report.failed} 个失败")
                else:
                    st.success(f"全部 {report.total} 个流程写入成功")
            except Exception as exc:
                st.error(f"批量写入异常：{exc}")

    report = st.session_state.get("batch_write_report")
    write_results_table = ensure_write_results_table(enabled_targets, settings=settings)

    st.subheader("写入结果")
    ok_count = sum(1 for r in write_results_table.rows if r.write_result == "成功")
    fail_count = sum(1 for r in write_results_table.rows if r.write_result == "失败")
    st.caption(f"成功 {ok_count} · 失败 {fail_count} · 共 {len(write_results_table.rows)} 个流程")

    editor_rows = table_to_dataframe_rows(write_results_table)
    edited_df = st.data_editor(
        editor_rows,
        column_config={
            "target_key": None,
            "流程": st.column_config.TextColumn("流程", disabled=True),
            "应用名": st.column_config.TextColumn("应用名", disabled=True),
            "OA表单": st.column_config.TextColumn("OA表单", disabled=True),
            "表单&流程错误": st.column_config.SelectboxColumn(
                "表单&流程错误",
                options=["是", "否"],
                required=True,
                help="人工标记需重写的表单或流程问题，默认否",
            ),
            "写入结果": st.column_config.TextColumn("写入结果", disabled=True),
            "失败原因": st.column_config.TextColumn("失败原因", disabled=True, width="large"),
        },
        use_container_width=True,
        hide_index=True,
        key="batch_write_results_editor",
    )
    write_results_table = dataframe_rows_to_table(edited_df, write_results_table)
    save_write_results(write_results_table, settings=settings)

    col_retry_fail, col_retry_mark, col_export, _ = st.columns([1, 1, 1, 1])
    with col_retry_fail:
        retry_failed_clicked = st.button("写入失败批量重写", key="retry_failed_writes")
    with col_retry_mark:
        retry_marked_clicked = st.button("表单&流程错误批量重写", key="retry_marked_writes")
    with col_export:
        excel_path = export_write_results_to_excel(write_results_table, settings=settings)
        st.download_button(
            "导出 Excel",
            data=write_results_excel_bytes(write_results_table),
            file_name=excel_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="export_write_results_excel",
        )

    def _run_partial_rewrite(targets_to_retry: list[TemplateGenerationTarget], label: str) -> None:
        _sync_token_from_env()
        fresh_settings = reload_settings()
        token_val = (st.session_state.get("fighter_auth_token") or fresh_settings.shenbi_api_token or "").strip()
        if not token_val:
            st.error("请填写 fighter-auth-token")
            return
        if not targets_to_retry:
            st.warning(f"没有可重写的流程（{label}）")
            return
        progress = st.progress(0.0, text=f"准备{label}…")

        def _on_write_progress(current: int, total: int, name: str) -> None:
            ratio = current / total if total else 0.0
            progress.progress(min(ratio, 1.0), text=f"({current}/{total}) {name}")

        try:
            api_settings = fresh_settings.model_copy(
                update={
                    "guide_parse_use_cache": True,
                    "shenbi_ssl_verify": ssl_verify,
                    "shenbi_session_cookie": session_cookie,
                }
            )
            with st.spinner(f"{label}：{len(targets_to_retry)} 个流程…"):
                partial_report = run_template_batch_write(
                    targets_to_retry,
                    settings=api_settings,
                    token=token_val,
                    on_progress=_on_write_progress,
                )
            write_results_table_local = ensure_write_results_table(enabled_targets, settings=settings)
            write_results_table_local = apply_batch_report_to_table(write_results_table_local, partial_report)
            save_write_results(write_results_table_local, settings=settings)
            progress.progress(1.0, text="重写完成")
            st.success(f"{label}完成：成功 {partial_report.succeeded}/{partial_report.total}")
            st.rerun()
        except Exception as exc:
            st.error(f"{label}异常：{exc}")

    if retry_failed_clicked:
        targets_to_retry = select_targets_for_rewrite(
            write_results_table, enabled_targets, mode="failed"
        )
        _run_partial_rewrite(targets_to_retry, "写入失败批量重写")

    if retry_marked_clicked:
        targets_to_retry = select_targets_for_rewrite(
            write_results_table, enabled_targets, mode="form_flow_error"
        )
        _run_partial_rewrite(targets_to_retry, "表单&流程错误批量重写")

    if report:
        tree_ok = sum(1 for item in report.items if item.success and item.tree_confirmed)
        with st.expander("最近一次批量写入摘要", expanded=False):
            st.caption(
                f"成功 {report.succeeded} · 失败 {report.failed} · 共 {report.total} · "
                f"环节树已确认 {tree_ok}"
            )

    with st.expander("可写入流程清单（与匹配结果同步）", expanded=False):
        st.dataframe(
            [_target_list_row(t) for t in enabled_targets],
            use_container_width=True,
            hide_index=True,
        )

    disabled_targets = [t for t in all_targets if not t.enabled]
    if disabled_targets:
        with st.expander(f"暂不可写入（{len(disabled_targets)} 个）", expanded=False):
            st.dataframe(
                [_target_list_row(t) for t in disabled_targets],
                use_container_width=True,
                hide_index=True,
            )

with tab_match:
    st.caption(
        "任务表 C 列流程 → 野马集团 OA 表单匹配结果（一行一流程）。"
        " B 列多个表单名匹配后合并显示；未匹配标注「新增」。"
    )
    match_rows = []
    for item in template_report.rows:
        row = item.row
        task_row = pending_by_key.get((row.sheet_name, row.row_index))
        match_rows.append(
            {
                "Sheet": row.sheet_name,
                "板块": row.sector_label,
                "序号": row.seq,
                "流程(C列)": row.backend_flow,
                "原系统表单(B)": row.original_form_name.replace("\n", " / "),
                "OA匹配表单": item.oa_forms_display,
                "新增": "是" if item.is_new_form else "否",
                "完成状态(F)": (task_row.completion_status if task_row else "") or "—",
                "流程图标题": item.flowchart_title or "—",
                "可写入键": item.target_key,
                "备注": item.skip_reason or row.remark,
            }
        )
    st.dataframe(match_rows, use_container_width=True, hide_index=True)

    st.subheader("写入目标清单")
    st.dataframe(
        [_target_list_row(t) for t in all_targets],
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("任务表待写入统计", expanded=False):
        st.write(f"共 {len(pending_rows)} 行待写入 C 列（第 3 行起，已排除 F 列「已完成/进行中」）")
        by_sheet: dict[str, int] = {}
        for row in pending_rows:
            by_sheet[row.sheet_name] = by_sheet.get(row.sheet_name, 0) + 1
        st.json(by_sheet)

payloads = st.session_state.get("shenbi_payloads")
if payloads:
    st.markdown("---")
    st.subheader("接口 JSON 预览")
    _chain = payloads.get("semantic_flow_chain") or []
    if _chain:
        st.caption("流程环节：" + " → ".join(_chain))
    _source = payloads.get("task_tree_source") or ""
    if _source:
        st.caption(f"环节树来源：`{_source}`")
    if payloads.get("app_display_name"):
        st.caption(f"应用显示名：{payloads['app_display_name']}")
    model = payloads.get("model")
    if model:
        with st.expander("查看完整 model JSON", expanded=False):
            st.code(json.dumps(model, ensure_ascii=False, indent=2), language="json")
        with st.expander("查看实际提交格式（wfSimpleDefaultModel Base64）", expanded=False):
            wire = prepare_save_payload(model)
            preview = {"wfSimpleDefaultModel": (wire.get("wfSimpleDefaultModel") or "")[:80] + "…"}
            st.code(json.dumps(preview, ensure_ascii=False, indent=2), language="json")

st.markdown("---")
st.markdown(
    """
**说明**

1. **单独写入**：按任务表 C 列流程生成 JSON；有 OA 表单则复用字段，无则「新增」最小表单。
2. **批量写入**：全量 VLM 解析后，按任务表清单逐条写入（一行一流程）。
3. **匹配结果**：C 列流程与 OA 表单匹配；未匹配标注「新增」，可仅含流程图。
4. **SESSION Cookie**：建议填写浏览器完整 Cookie；未填写时会自动在 Cookie 中附带 token 尝试发布。若工作台仍提示「查无该流程定义信息」，请点击 **批量发布流程** 或运行 `python scripts/republish_all_registries.py`。
"""
)
