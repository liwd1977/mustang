# Mustang Delivery — 野马集团智能交付工作台

基于 Streamlit 的轻量 AI 交付工具，用于从 Word 办事指南提取流程图、通过通义千问 VLM 识别图元，并从外部网址语义匹配表单，输出流程配置数据包。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY（DashScope）
streamlit run app/main.py
```

## 文档

- [技术方案（修订版）](docs/ARCHITECTURE.md)

## 当前范围

- ✅ Word 流程图提取、VLM 识别、表单语义匹配、配置 JSON 输出
- ⏸ 拓扑重建 / 神笔 API 刷入（待平台资料到位）
