"""原始表单提取：调试 OA 表单、本地存储与勘误清单。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config.settings import reload_settings
from core.form.batch_runner import (
    is_batch_running,
    load_batch_progress,
    log_path,
    progress_path,
    start_batch_worker,
    start_retry_failed_worker,
)
from core.form.extraction_status import load_extraction_status, save_extraction_status
from core.form.form_extractor import extract_forms_batch, extract_raw_form
from core.form.form_store import filter_unextracted_targets, get_forms_dir, list_forms, load_form, search_forms
from core.form.reconciliation import run_reconciliation
from core.form.task_table import filter_targets_by_sheets, iter_oa_form_names, load_task_table, summarize_task_table
from core.form.template_search import fetch_oa_catalog
from core.oa.connectivity import check_oa_reachable
from core.oa.playwright_env import check_playwright_chromium, resolve_oa_proxy
from core.ui.progress_timing import should_show_timing, timing_label
from core.workflow.result_store import load_result

settings = reload_settings()
task_table_path = settings.flow_task_table_path

st.set_page_config(page_title="原始表单提取", layout="wide")
st.title("原始表单提取")
st.caption("从野马 OA 提取原始 CAP4 表单，本地存储供后续工作流 JSON 生成使用")

tab_extract, tab_batch, tab_search, tab_reconcile = st.tabs(
    ["单表单提取", "任务表批量提取", "表单查询", "勘误清单"]
)

with st.sidebar:
    st.header("数据源")
    st.markdown(f"**任务表**\n\n`{task_table_path}`")
    if task_table_path.is_file():
        st.success("任务表已就绪")
    else:
        st.error("任务表未找到")

    st.markdown(f"**表单存储**\n\n`{get_forms_dir()}`")
    stored = list_forms()
    st.caption(f"已存储 {len(stored)} 个表单")

    parse_result = load_result()
    if parse_result:
        st.success("已加载办事指南解析缓存")
    else:
        st.warning("尚无办事指南解析缓存，勘误清单中文档流程可能不完整")

    pw_ok, pw_msg = st.session_state.get("pw_check", (None, ""))
    if pw_ok is None:
        pw_ok, pw_msg = check_playwright_chromium()
        st.session_state.pw_check = (pw_ok, pw_msg)
    if pw_ok:
        st.success("Playwright 浏览器已就绪")
    else:
        st.error(pw_msg)

    oa_ok, oa_msg = st.session_state.get("oa_check", (None, ""))
    if oa_ok is None:
        oa_ok, oa_msg = check_oa_reachable(settings)
        st.session_state.oa_check = (oa_ok, oa_msg)
    if oa_ok:
        st.success(oa_msg)
    else:
        st.error(oa_msg)
        if st.button("重新检测 OA 连通性", use_container_width=True):
            oa_ok, oa_msg = check_oa_reachable(settings)
            st.session_state.oa_check = (oa_ok, oa_msg)
            st.rerun()

    st.caption(f"OA 账号：{settings.oa_username or '（未配置）'}")
    proxy = resolve_oa_proxy(settings)
    if proxy:
        st.caption(f"OA 代理：{proxy}")
    else:
        st.warning("未配置 OA 代理。浏览器能访问 OA 但提取失败时，请在 .env 设置 OA_PROXY=http://127.0.0.1:7897")
    st.caption(f"超时：{settings.oa_playwright_timeout_ms // 1000}s")


def _render_form_record(record) -> None:
    st.markdown(f"**模板名称：** {record.template_name}")
    c1, c2, c3 = st.columns(3)
    c1.metric("字段数", len(record.fields))
    c2.metric("提取状态", "成功" if record.success else "失败")
    c3.metric("提取时间", record.extracted_at or "—")
    if record.template_id:
        st.caption(f"模板 ID：{record.template_id}")
    if record.linked_workflows:
        st.caption(f"关联流程：{', '.join(record.linked_workflows)}")
    if record.error:
        st.error(record.error)
    if record.fields:
        with st.expander("字段列表", expanded=True):
            for i, field in enumerate(record.fields, 1):
                t = field.input_type or field.tag or "-"
                st.markdown(f"{i}. **{field.label}** `[{t}]`")
    with st.expander("原始 JSON", expanded=False):
        st.code(json.dumps(record.model_dump(), ensure_ascii=False, indent=2), language="json")


with tab_extract:
    st.subheader("单表单提取")
    st.markdown("输入 OA 公共模板中的表单名称，登录 OA 后提取全部可见字段并保存到本地。")

    default_name = "集团公司部门费用报销单"
    form_name = st.text_input("OA 表单名称", value=default_name)
    extract_one = st.button(
        "提取并保存",
        type="primary",
        disabled=not form_name.strip() or not st.session_state.get("oa_check", (False, ""))[0],
    )

    if extract_one:
        with st.spinner(f"正在提取「{form_name}」…"):
            try:
                record = extract_raw_form(form_name.strip())
                st.success(f"已保存至 {get_forms_dir()}")
                _render_form_record(record)
            except Exception as exc:
                err = str(exc)
                if "Executable doesn't exist" in err:
                    st.error(
                        "提取失败：Playwright 浏览器未安装。\n\n"
                        "请在终端执行：\n"
                        "`venv\\Scripts\\python.exe -m playwright install chromium`"
                    )
                elif "ERR_ABORTED" in err or "Timeout" in err or "超时" in err or "无法连接 OA" in err or "无法打开" in err:
                    st.error(
                        f"提取失败：{exc}\n\n"
                        "若为页面跳转中断，请**重启 Streamlit** 后重试；"
                        "并确认 OA 代理与侧边栏连通性正常。"
                    )
                else:
                    st.error(f"提取失败：{exc}")


with tab_batch:
    st.subheader("任务表批量提取")
    st.markdown(
        "遍历任务表 **全部 Sheet** 的 B 列（排除「新增」），依次从 OA 提取表单并保存。"
        "支持按 Sheet 筛选、跳过已成功项（断点续跑）。"
    )

    if not task_table_path.is_file():
        st.error("任务表不存在，请将 Excel 放入 data/ 目录")
    else:
        try:
            task_rows = load_task_table(task_table_path)
            sheet_stats = summarize_task_table(task_rows)
            all_sheets = list(sheet_stats.keys())
            oa_targets = iter_oa_form_names(task_rows)
            st.info(
                f"共 {len(sheet_stats)} 个 Sheet · 任务行 {len(task_rows)} 条 · "
                f"可提取 OA 表单 {len(oa_targets)} 个"
            )
            with st.expander("各 Sheet 统计", expanded=True):
                st.dataframe(
                    [
                        {"Sheet": name, "任务行": v["rows"], "可提取表单": v["forms"]}
                        for name, v in sheet_stats.items()
                    ],
                    use_container_width=True,
                )
        except Exception as exc:
            st.error(f"读取任务表失败：{exc}")
            task_rows = []
            oa_targets = []
            all_sheets = []

        selected_sheets = st.multiselect(
            "选择要提取的 Sheet（默认全选）",
            options=all_sheets,
            default=all_sheets,
        )
        skip_existing = st.toggle(
            "断点续跑（跳过已有本地记录的表单）",
            value=True,
            help="开启后「开始批量提取」仅访问从未提取过的表单；关闭则强制全量重跑所选 Sheet",
        )
        use_background = st.toggle(
            "后台运行（推荐）",
            value=True,
            help="独立子进程执行；Streamlit 断连或锁屏后任务仍继续，并自动阻止系统休眠",
        )

        scoped = filter_targets_by_sheets(task_rows, selected_sheets or None)
        if skip_existing:
            pending, skipped_count = filter_unextracted_targets(scoped)
        else:
            pending, skipped_count = list(scoped), 0
        ext_status = save_extraction_status(scoped)
        extractable_count = len(scoped)
        if skip_existing:
            st.caption(
                f"本次将提取 **{len(pending)}** 个未提取表单"
                + (f"（已有本地记录 {skipped_count} 个，含已成功与失败）" if skipped_count else "")
            )
        else:
            st.caption(f"本次将全量重跑 **{len(pending)}** 个可提取表单（未启用断点续跑）")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("累计已成功", ext_status["stored_success_unique"])
        c2.metric("累计失败", ext_status["stored_failed_unique"])
        c3.metric("从未提取", ext_status["stored_missing_unique"])
        c4.metric("可提取表单", extractable_count)
        st.caption(
            "「开始批量提取」处理**未提取**表单（断点续跑）；「重试失败」仅处理下方失败清单。"
            "下方进度区的成功/失败/跳过指**本次运行**。"
        )

        failed_items = ext_status.get("failed", [])
        seen_forms: set[str] = set()
        display_failed: list[dict] = []
        for x in failed_items:
            key = x.get("form", "")
            if key in seen_forms:
                continue
            seen_forms.add(key)
            display_failed.append(x)
        with st.expander(f"表单提取失败清单（{len(display_failed)} 个）", expanded=bool(display_failed)):
            if display_failed:
                st.dataframe(
                    [
                        {
                            "Sheet": x.get("sheet"),
                            "表单": x.get("form"),
                            "失败原因": (x.get("error") or "")[:200],
                        }
                        for x in display_failed
                    ],
                    use_container_width=True,
                )
            else:
                st.success("暂无提取失败的表单")
            st.caption(f"状态文件：`{get_forms_dir() / 'extraction_status.json'}`")

        oa_ok = st.session_state.get("oa_check", (False, ""))[0]
        running = is_batch_running()
        prog = load_batch_progress()

        if running:
            st.warning(
                "后台批量任务运行中（已启用防休眠）。可关闭本页或锁屏，任务会继续执行；"
                "完成后点击「刷新进度」查看结果。"
            )
        elif prog.get("status") == "interrupted":
            st.error(
                f"上次任务中断：{prog.get('last_error', '未知原因')}。"
                "若因 PC 休眠导致，请使用「后台运行」并开启断点续跑后重新开始。"
            )

        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
            batch_clicked = st.button(
                "开始批量提取",
                type="primary",
                disabled=not pending or not oa_ok or running,
                help="全量提取未下载的表单，支持断点续跑",
            )
        with btn_col2:
            refresh_clicked = st.button("刷新进度", disabled=not prog.get("status") or prog.get("status") == "idle")
        with btn_col3:
            retry_failed_clicked = st.button(
                "重试失败",
                disabled=(
                    ext_status["stored_failed_unique"] == 0
                    or not oa_ok
                    or running
                ),
                help="仅重新提取「表单提取失败清单」中的表单",
            )

        batch_progress = st.progress(0.0, text="等待开始…")
        batch_metrics = st.empty()
        batch_detail = st.empty()

        def _render_progress_from_file() -> None:
            p = load_batch_progress()
            status = p.get("status", "idle")
            done = int(p.get("done") or 0)
            total = int(p.get("total") or 0)
            timing = ""
            if should_show_timing(total):
                timing = timing_label(
                    started_at=p.get("started_at"),
                    finished_at=p.get("finished_at"),
                    running=status == "running",
                )
            if status == "running" and total > 0:
                bar_text = f"提取进度 {done}/{total}"
                if timing:
                    bar_text = f"{bar_text} · {timing}"
                batch_progress.progress(min(done / total, 1.0), text=bar_text)
            elif status == "completed":
                bar_text = "批量提取完成"
                if timing:
                    bar_text = f"{bar_text} · {timing}"
                batch_progress.progress(1.0, text=bar_text)
            timing_metric = timing.replace("已耗时 ", "").replace("总耗时 ", "") if timing else "—"
            batch_metrics.markdown(
                f"**状态** {status} · **本次成功** {p.get('success', 0)} · "
                f"**本次失败** {p.get('failed', 0)} · **本次跳过** {p.get('skipped', 0)} · "
                f"**耗时** {timing_metric}"
            )
            if p.get("cumulative_success_unique") is not None:
                batch_detail.markdown(
                    f"累计已成功 **{p.get('cumulative_success_unique', 0)}** / "
                    f"可提取表单 **{p.get('cumulative_extractable_targets', p.get('cumulative_unique_targets', 0))}** · "
                    f"仍失败 **{p.get('cumulative_failed_unique', 0)}** · "
                    f"未提取 **{p.get('cumulative_missing_unique', 0)}**"
                )
            elif p.get("current"):
                batch_detail.info(f"当前：{p['current']}")
            elif p.get("last_error"):
                batch_detail.warning(p["last_error"])

        if running or prog.get("status") in {"running", "completed", "interrupted", "failed"}:
            _render_progress_from_file()
            st.caption(f"进度文件：`{progress_path()}` · 日志：`{log_path()}`")

        if refresh_clicked:
            st.rerun()

        if retry_failed_clicked:
            try:
                if use_background:
                    proc = start_retry_failed_worker()
                    if proc is None:
                        st.warning("已有后台任务在运行")
                    else:
                        st.success(f"已启动失败表单重试（PID {proc.pid}）")
                        st.rerun()
                else:
                    from core.form.batch_runner import run_retry_failed_job

                    with st.spinner("重试失败表单…"):
                        batch = run_retry_failed_job()
                    st.success(
                        f"重试完成：成功 {batch.success} / 失败 {batch.failed}"
                    )
                    st.session_state.batch_result = batch.model_dump()
                    st.rerun()
            except Exception as exc:
                st.error(f"重试失败：{exc}")

        if batch_clicked and pending:
            try:
                if use_background:
                    proc = start_batch_worker(
                        sheet_names=selected_sheets or None,
                        skip_existing=skip_existing,
                    )
                    if proc is None:
                        st.warning("已有后台任务在运行")
                    else:
                        st.success(f"已启动后台批量提取（PID {proc.pid}），已阻止系统休眠")
                        st.rerun()
                else:
                    from datetime import datetime

                    sync_started = datetime.now().isoformat(timespec="seconds")
                    with st.spinner("同步批量提取中（已阻止系统休眠）…"):
                        batch = extract_forms_batch(
                            task_rows,
                            sheet_names=selected_sheets or None,
                            skip_existing=skip_existing,
                        )
                    sync_finished = datetime.now().isoformat(timespec="seconds")
                    bar_text = "批量提取完成"
                    if should_show_timing(batch.total):
                        timing = timing_label(
                            started_at=sync_started,
                            finished_at=sync_finished,
                            running=False,
                        )
                        if timing:
                            bar_text = f"{bar_text} · {timing}"
                    batch_progress.progress(1.0, text=bar_text)
                    st.success(
                        f"批量提取完成：成功 {batch.success} / 失败 {batch.failed} / 跳过 {batch.skipped}"
                    )
                    st.caption(f"报告路径：`{get_forms_dir() / 'batch_extract_report.json'}`")
                    st.session_state.batch_result = batch.model_dump()
            except Exception as exc:
                st.error(f"批量提取失败：{exc}")

        if running:
            import time

            time.sleep(3)
            st.rerun()

        batch_data = st.session_state.get("batch_result")
        if batch_data and batch_data.get("records"):
            with st.expander("最近一次批量结果", expanded=False):
                st.dataframe(
                    [
                        {
                            "Sheet": r.get("sheet_name"),
                            "表单": r.get("template_name"),
                            "成功": r.get("success"),
                            "字段数": len(r.get("fields") or []),
                            "错误": r.get("error"),
                        }
                        for r in batch_data["records"]
                    ],
                    use_container_width=True,
                )


with tab_search:
    st.subheader("表单查询")
    query = st.text_input("模糊搜索表单名称", placeholder="例如：费用报销")
    hits = search_forms(query, limit=30)

    if not hits:
        st.info("暂无匹配表单，请先在「单表单提取」或「任务表批量提取」中提取。")
    else:
        st.caption(f"匹配 {len(hits)} 条")
        for item in hits:
            label = item.get("template_name", "")
            score = item.get("match_score")
            title = f"{label}" + (f"  (相似度 {score})" if score else "")
            with st.expander(title, expanded=False):
                record = load_form(label)
                if record:
                    _render_form_record(record)
                else:
                    st.warning("索引存在但文件缺失")


with tab_reconcile:
    st.subheader("勘误清单")
    st.markdown(
        "对比任务表 C/D 列流程与办事指南解析结果（语义匹配），"
        "并检查任务表 B 列表单是否在 OA 公共模板中存在。"
    )

    col_a, col_b = st.columns(2)
    use_llm = col_a.toggle("启用 LLM 语义匹配", value=settings.llm_configured, disabled=not settings.llm_configured)
    refresh_catalog = col_b.toggle("刷新 OA 模板目录", value=False)

    gen_clicked = st.button("生成勘误清单", type="primary", disabled=not task_table_path.is_file())

    if gen_clicked:
        with st.spinner("正在生成勘误清单…"):
            try:
                rows = load_task_table(task_table_path)
                report = run_reconciliation(
                    rows,
                    parse_result=parse_result,
                    use_llm=use_llm,
                    refresh_oa_catalog=refresh_catalog,
                )
                st.session_state.reconcile_report = report.model_dump()
                out_path = settings.output_dir / "forms" / "reconciliation_report.json"
                out_path.write_text(
                    json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                st.success(f"已保存：{out_path}")
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    report_data = st.session_state.get("reconcile_report")
    if report_data:
        report = report_data
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("文档流程数", report.get("doc_workflow_total", 0))
        m2.metric("任务表流程数", report.get("excel_workflow_total", 0))
        m3.metric("未匹配(任务表)", len(report.get("unmatched_excel") or []))
        m4.metric("OA 缺失表单", len(report.get("missing_oa_forms") or []))

        st.markdown("#### 1. 任务表流程与文档流程不匹配")
        matches = report.get("workflow_matches") or []
        mismatch_rows = [
            {
                "任务表流程": m.get("excel_name"),
                "来源列": m.get("excel_source"),
                "匹配文档流程": m.get("matched_doc_name") or "—",
                "置信度": m.get("confidence"),
                "状态": m.get("status"),
                "说明": m.get("reason"),
            }
            for m in matches
            if m.get("status") != "matched"
        ]
        if mismatch_rows:
            st.dataframe(mismatch_rows, use_container_width=True)
        else:
            st.success("任务表 C/D 列流程均已匹配到文档流程")

        unmatched_doc = report.get("unmatched_doc") or []
        if unmatched_doc:
            st.markdown("**文档中存在、任务表未收录的流程：**")
            st.write(", ".join(unmatched_doc[:50]))
            if len(unmatched_doc) > 50:
                st.caption(f"…共 {len(unmatched_doc)} 条")

        st.markdown("#### 2. OA 中不存在的表单（任务表 B 列）")
        missing = report.get("missing_oa_forms") or []
        if missing:
            st.dataframe(
                [
                    {
                        "Sheet": m.get("sheet_name"),
                        "序号": m.get("task_seq"),
                        "板块": m.get("sector"),
                        "表单名称": m.get("form_name"),
                        "关联流程": ", ".join(m.get("linked_workflows") or []),
                    }
                    for m in missing
                ],
                use_container_width=True,
            )
        else:
            st.success("任务表中的表单均在 OA 公共模板中找到（或仅有模糊匹配项）")

        with st.expander("完整勘误 JSON", expanded=False):
            st.code(json.dumps(report, ensure_ascii=False, indent=2), language="json")

    elif not gen_clicked:
        st.info("点击「生成勘误清单」开始对比。建议先完成办事指南流程解析并确保 OA 可访问。")

        if refresh_catalog or st.button("仅刷新 OA 模板目录"):
            with st.spinner("正在拉取 OA 公共模板目录…"):
                try:
                    catalog = fetch_oa_catalog(refresh_oa_catalog=True)
                    if catalog.success:
                        total = sum(len(v) for v in catalog.catalog.values())
                        st.success(f"已缓存 {total} 个模板，分类 {len(catalog.subdirs)} 个")
                    else:
                        st.error(catalog.error_message or "拉取失败")
                except Exception as exc:
                    st.error(f"拉取失败：{exc}")
