"""野马集团低代码应用智能交付工作台。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config.settings import get_settings

st.set_page_config(
    page_title="野马智能交付工作台",
    page_icon="🐎",
    layout="wide",
    initial_sidebar_state="expanded",
)

settings = get_settings()

st.title("野马集团 · 智能交付工作台")
st.caption("AI-00 | 办事指南解析 · 流程图识别 · 配置数据预览")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("默认文档", settings.guide_docx_name)
with col2:
    st.metric("VLM 模型", settings.vlm_model_name)
with col3:
    st.metric("API 状态", "已配置" if settings.llm_configured else "未配置")
    if settings.llm_configured:
        st.caption("OPENAI_API_KEY / QWEN_API_KEY 已从环境变量或 .env 加载（qwen-vl-max 与 qwen-plus 共用）")

st.markdown(
    """
### 功能导航

请从左侧页面菜单进入：

1. **流程解析** — 解析办事指南 Word（第二~十板块），提取流程图并用 VLM 识别节点/条件
2. **原始表单提取** — 从野马 OA 提取 CAP4 原始表单，生成流程对齐勘误清单
3. **流程写入** — 生成神笔平台 model-save JSON 并一键写入
4. **系统设置** — 查看环境配置与文件路径

### 快速开始

1. 将 `野马集团办事指南20260816.docx` 放入 `data/` 目录
2. 在 `.env` 中配置 `OPENAI_API_KEY`（DashScope）
3. 进入「流程解析」页解析办事指南；进入「原始表单提取」页提取 OA 表单
"""
)

default_doc = settings.guide_docx_path
if default_doc.is_file():
    st.success(f"已检测到默认文档：`{default_doc}`")
else:
    st.warning(f"默认文档尚未放入：`{default_doc}`")
