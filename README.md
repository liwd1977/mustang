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

## 功能模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 交付工作台 | `app/main.py` | Streamlit 入口 |
| 流程解析页 | `app/pages/01_流程解析.py` | Word 解析 + VLM 识别展示 |
| Word 解析 | `core/workflow/word_parser.py` | 提取第二~十板块正文与流程图 |
| VLM 识别 | `core/workflow/vlm_recognizer.py` | 通义千问流程图结构化识别 |
| 解析管线 | `core/workflow/pipeline.py` | 串联 Word + VLM，输出 JSON 快照 |

## 办事指南文档

将 `野马集团办事指南2026080301.docx` 放入 `data/` 目录。

命令行测试：

```bash
python scripts/test_word_parser.py
streamlit run app/main.py
```

- ✅ Word 流程图提取、VLM 识别、表单语义匹配、配置 JSON 输出
- ⏸ 拓扑重建 / 神笔 API 刷入（待平台资料到位）
