"""流程写入神笔平台。"""



from __future__ import annotations



import sys

from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



import json



import streamlit as st



from core.config.settings import env_file_mtime, reload_settings

from core.workflow.shenbi_builder import (

    WORKFLOW_CATALOG,

    build_workflow_payloads,

    display_workflow_name,

    save_generated_payloads,

)

from core.workflow.shenbi_client import (

    ShenbiApiError,

    apply_registry_to_payloads,

    check_shenbi_auth,

    is_legacy_registry,

    load_workflow_registry,

    prepare_save_payload,

    registry_app_verified,

    save_workflow_models,

)





def _sync_token_from_env() -> None:

    """检测 .env 变更（含 Cursor 直接改 token），同步侧边栏。"""

    mtime = env_file_mtime()

    if mtime is None:

        return

    if st.session_state.get("_env_mtime") == mtime:

        return



    fresh = reload_settings()

    st.session_state["_env_mtime"] = mtime

    st.session_state["fighter_auth_token"] = fresh.shenbi_api_token





_sync_token_from_env()

settings = reload_settings()



st.set_page_config(page_title="流程写入", layout="wide")

st.title("流程写入 · 神笔平台")

st.caption("基于办事指南解析结果、OA 表单与配置规律，程序化生成 model-save JSON 并一键写入神笔平台")



WORKFLOW_OPTIONS = list(WORKFLOW_CATALOG.keys())



with st.sidebar:

    st.subheader("神笔平台连接")

    token = st.text_input(

        "fighter-auth-token",

        key="fighter_auth_token",

        type="password",

        help="默认读 .env；Cursor 更新 SHENBI_API_TOKEN 后，下次点击页面任意按钮即自动同步",

    )

    st.text_input("API 地址", value=settings.shenbi_base_url, disabled=True)

    st.text_input("默认 appId（仅兼容旧配置）", value=settings.shenbi_app_id, disabled=True)

    ssl_verify = st.toggle(

        "校验 HTTPS 证书",

        value=settings.shenbi_ssl_verify,

        help="神笔平台证书过期时可关闭（仅临时调试）",

    )

    st.caption("写入：tBaidaApp/saveOrUpdate → model-save")

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



selected = st.selectbox("选择目标工作流", options=WORKFLOW_OPTIONS, index=0)

meta = WORKFLOW_CATALOG[selected]

st.caption(f"关联 OA 表单：{meta['form_slug']} · 流程名：{display_workflow_name(selected)}")



_registry = load_workflow_registry(selected, settings=settings)

_app_name = display_workflow_name(selected)

if _registry and registry_app_verified(_registry):

    st.caption(

        f"将更新已有应用/流程（应用：{_registry.get('app_mc', _app_name)} · "

        f"appId={_registry.get('app_id', '')[:8]}… · procId={_registry.get('proc_id', '')[:8]}…）"

    )

elif is_legacy_registry(_registry, settings=settings):

    st.warning(

        f"检测到旧版 registry：流程曾挂在 .env 默认 appId（{_registry.get('app_id', '')[:8]}…）下，"

        f"「我的应用」中不会出现独立应用。下次一键写入将**自动创建应用「{_app_name}」**并新建表单/流程。"

    )

else:

    st.caption(

        f"未找到本地 registry，将新建应用「{_app_name}」，并在应用内创建表单与流程"

    )


col_gen, col_write = st.columns([1, 1])

with col_gen:

    generate_clicked = st.button("生成接口 JSON", type="primary")

with col_write:

    write_clicked = st.button("一键写入神笔平台", type="secondary")



if generate_clicked:

    try:

        with st.spinner("正在构建流程 model-save JSON…"):

            payloads = build_workflow_payloads(selected, settings=settings)

            payloads = apply_registry_to_payloads(payloads, selected, settings=settings)

            out_path = save_generated_payloads(payloads, settings=settings)

        st.session_state.shenbi_payloads = payloads

        st.session_state.shenbi_out_path = str(out_path)

        st.success("JSON 已生成")

    except Exception as exc:

        st.error(f"生成失败：{exc}")



payloads = st.session_state.get("shenbi_payloads")



if payloads:

    st.subheader("接口 JSON 预览")

    _chain = payloads.get("semantic_flow_chain") or []

    if _chain:

        st.caption("流程环节：" + " → ".join(_chain))

    _source = payloads.get("task_tree_source") or ""

    if _source:

        st.caption(f"环节树来源：`{_source}`")

    _bindings = payloads.get("dept_branch_bindings") or []

    if _bindings:

        with st.expander("8 路分支绑定（指南 seq → 环节名）", expanded=False):

            st.dataframe(_bindings, use_container_width=True, hide_index=True)

    for _warn in payloads.get("parse_warnings") or []:

        st.warning(_warn)

    model = payloads.get("model")

    if model:

        with st.expander("查看完整 model JSON", expanded=False):

            st.code(json.dumps(model, ensure_ascii=False, indent=2), language="json")

        with st.expander("查看实际提交格式（wfSimpleDefaultModel Base64）", expanded=False):

            wire = prepare_save_payload(model)

            preview = {

                "wfSimpleDefaultModel": (wire.get("wfSimpleDefaultModel") or "")[:80] + "…",

            }

            st.code(json.dumps(preview, ensure_ascii=False, indent=2), language="json")



if write_clicked:

    _sync_token_from_env()

    fresh_settings = reload_settings()

    token = (st.session_state.get("fighter_auth_token") or fresh_settings.shenbi_api_token or "").strip()

    if not token.strip():

        st.error("请填写 fighter-auth-token")

    else:

        results: list[dict] = []

        try:

            api_settings = fresh_settings.model_copy(update={"shenbi_ssl_verify": ssl_verify})

            with st.spinner("正在生成最新 JSON 并写入神笔平台…"):

                payloads = build_workflow_payloads(selected, settings=api_settings)

                payloads = apply_registry_to_payloads(payloads, selected, settings=api_settings)

                out_path = save_generated_payloads(payloads, settings=api_settings)

                write_result = save_workflow_models(

                    payloads,

                    workflow_name=selected,

                    settings=api_settings,

                    token=token,

                )

            results = write_result["results"]

            st.session_state.shenbi_payloads = {

                **payloads,

                "model": write_result["model"],

            }

            st.session_state.shenbi_out_path = str(out_path)

            st.success("已成功写入神笔平台")

            if write_result.get("app_mc"):

                st.info(f"应用名称：**{write_result['app_mc']}** · appId `{write_result.get('app_id', '')}`")

            complex_steps = [r for r in results if r.get("step") == "复杂流程"]
            if complex_steps:
                st.info(complex_steps[-1].get("detail", ""))

            if write_result.get("proc_id"):
                st.caption(
                    f"当前流程 procId `{write_result.get('proc_id', '')}` · "
                    f"formId `{write_result.get('form_id', '')}` · "
                    "刷新设计器后应看到完整并行分支（非「办理start」空壳）"
                )

            auto_steps = [r for r in results if r.get("step") == "自动重建"]
            if auto_steps:
                st.info(
                    f"已自动重建流程 · formId `{write_result.get('form_id', '')}` · "
                    f"procId `{write_result.get('proc_id', '')}`"
                )

            model_check = write_result.get("model_check") or {}

            persisted = write_result.get("persisted_check") or {}

            if model_check:

                st.caption(f"本地模型：{model_check.get('message', '')}")

            if persisted:

                st.caption(f"平台回读：{persisted.get('message', '')}")

            write_warn = [
                r for r in results if r.get("step") == "写入结论" and r.get("mode") == "warn"
            ]
            if write_warn:
                st.warning(write_warn[-1].get("detail", ""))

        except ShenbiApiError as exc:

            st.error(str(exc))

            if exc.body is not None:

                with st.expander("API 响应详情", expanded=False):

                    st.json(exc.body if isinstance(exc.body, dict) else {"raw": str(exc.body)})

        except Exception as exc:

            st.error(f"写入异常：{exc}")



        if results:

            with st.expander("写入详情", expanded=False):

                for item in results:

                    step = item.get("step", "")

                    mode = item.get("mode", "")

                    if item.get("detail"):

                        icon = "✓" if mode in {"ok", "local", "create", "update", "reuse", "rebind"} else "⚠"

                        st.markdown(f"- {step} · {mode}：{item['detail']} {icon}")

                        continue

                    extra = ""

                    if item.get("app_mc"):

                        extra = f" ({item['app_mc']})"

                    elif item.get("app_id"):

                        extra = f" (appId={str(item['app_id'])[:8]}…)"

                    st.markdown(f"- {step} · {mode}{extra} ✓")



elif payloads and st.session_state.get("shenbi_out_path"):

    pass



st.markdown("---")

st.markdown(

    """

**说明**



1. **生成接口 JSON**：基于办事指南解析结果与 OA 表单字段，组装完整 model-save 模型。

2. **一键写入**：先 `tBaidaApp/saveOrUpdate` 创建应用 → model-save 表单/流程 → `tBaidaMenu/saveOrUpdate` 绑定流程菜单。

3. **智能写入**：复杂流程自动清空环节 id；若平台仍回传空壳环节树，将自动重建表单/流程并更新菜单，无需手工勾选。

"""

)


