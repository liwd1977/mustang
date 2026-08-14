"""系统设置与路径说明。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config.settings import get_settings

st.set_page_config(page_title="系统设置", layout="wide")
st.title("系统设置")

settings = get_settings()

st.subheader("路径与文档")
st.markdown(
    f"""
- 办事指南：`{settings.guide_docx_path}` — **{'已存在' if settings.guide_docx_path.is_file() else '未找到'}**
- 流程任务表：`{settings.flow_task_table_path}` — **{'已存在' if settings.flow_task_table_path.is_file() else '未找到'}**
- 表单存储：`{settings.forms_store_dir}`
- 流程解析缓存：`{settings.output_dir / 'workflows' / (settings.guide_output_stem + '_parse_result.json')}`
"""
)

st.subheader("解析与缓存")
_cache_mode = "批量（复用缓存）" if settings.guide_parse_use_cache else "开发试验（Word 更新自动重解析）"
st.markdown(
    f"""
- **GUIDE_PARSE_USE_CACHE**：`{settings.guide_parse_use_cache}` — {_cache_mode}
- **VLM_FLOW_LIMIT**：`{settings.vlm_flow_limit}`（0 = 不限制）
"""
)
st.caption(
    "开发阶段保持 GUIDE_PARSE_USE_CACHE=false，写入/生成 payload 时会读取办事指南最新 Word 并自动解析。"
    "批量处理时可设为 true 提速；文档更新后请在「流程解析」页手动重新解析。"
)

st.subheader("环境配置")
st.table(
    {
        "配置项": [
            "OA_BASE_URL",
            "OPENAI_API_KEY",
            "LLM_BASE_URL",
            "VLM_MODEL_NAME",
            "TEXT_MODEL_NAME",
            "DATA_DIR",
            "OUTPUT_DIR",
            "GUIDE_PARSE_USE_CACHE",
            "SHENBI_BASE_URL",
            "SHENBI_API_TOKEN",
        ],
        "当前值": [
            settings.oa_base_url,
            "已配置" if settings.llm_configured else "未配置",
            settings.llm_base_url,
            settings.vlm_model_name,
            settings.text_model_name,
            str(settings.data_dir),
            str(settings.output_dir),
            str(settings.guide_parse_use_cache),
            settings.shenbi_base_url,
            "已配置" if settings.shenbi_api_token else "未配置",
        ],
    }
)

st.subheader("启动命令")
st.code("streamlit run app/main.py", language="bash")
